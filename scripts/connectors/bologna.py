"""Connettore per il dataset ufficiale 'Zona a traffico limitato (ZTL)' del
Comune di Bologna (piattaforma OpenDataSoft).

Fonte: https://opendata.comune.bologna.it/explore/dataset/zona-a-traffico-limitato/
Formato: GeoJSON via API OpenDataSoft v2.1, licenza da verificare sul portale.

Verificato contro il dato reale: a differenza di Milano/Firenze/Torino, qui
NON c'è un nome leggibile per zona (niente "Area B" o simili) — ogni feature
ha solo properties (codice, ordinanza, stato, ente, area, più campi
codarco1..7/nomevia1..7 che sembrano riferirsi ad archi stradali usati per
costruire il perimetro). Probabile che il dataset descriva segmenti/tasselli
che compongono il perimetro ZTL piuttosto che zone già aggregate e nominate.

Per questo il campo `nome` viene costruito come "ZTL Bologna - zona {codice}"
invece di usare un nome ufficiale, che qui semplicemente non esiste nella
fonte. Geometria di tipo Polygon (come Firenze): serve ST_Multi.
"""
import json

import requests
from psycopg2.extras import execute_values

from scripts.common import content_hash, get_or_create_fonte, log_run

GEOJSON_URL = "https://opendata.comune.bologna.it/api/explore/v2.1/catalog/datasets/zona-a-traffico-limitato/exports/geojson"
COMUNE_ISTAT = "037006"  # Bologna
PAGE_SIZE = 500

UPSERT_SQL = """
insert into ztl_zone
    (comune_istat, fonte_id, external_id, nome, tipo, ordinanza_rif, geom, hash_contenuto)
values %s
on conflict (fonte_id, external_id) do update set
    nome = excluded.nome,
    tipo = excluded.tipo,
    ordinanza_rif = excluded.ordinanza_rif,
    geom = excluded.geom,
    hash_contenuto = excluded.hash_contenuto,
    aggiornato_il = now()
where ztl_zone.hash_contenuto is distinct from excluded.hash_contenuto
returning (xmax = 0) as inserted
"""
ROW_TEMPLATE = "(%s, %s, %s, %s, %s, %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s)"


def fetch():
    resp = requests.get(GEOJSON_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()


def run(conn):
    fonte_id = get_or_create_fonte(
        conn,
        comune_istat=COMUNE_ISTAT,
        nome_fonte="Comune di Bologna - Open Data (ZTL)",
        tipo="ufficiale_comunale",
        url=GEOJSON_URL,
        formato="geojson",
        licenza="da verificare sul portale opendata.comune.bologna.it",
    )

    try:
        data = fetch()
    except requests.RequestException as exc:
        print(f"[bologna] ERRORE: {exc}")
        log_run(conn, fonte_id, "errore", messaggio=str(exc))
        conn.commit()
        return

    rows = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        geom = feature.get("geometry")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue  # scarta geometrie inattese, non blocca l'intero run

        codice = props.get("codice")
        if codice is None:
            continue
        external_id = str(codice)
        stato = props.get("stato")
        ordinanza = props.get("ordinanza")
        nome = f"ZTL Bologna - zona {codice}"

        h = content_hash(nome, stato, ordinanza, geom)
        rows.append((COMUNE_ISTAT, fonte_id, external_id, nome, stato, ordinanza, json.dumps(geom), h))

    totali = len(rows)
    inserite = aggiornate = 0

    if rows:
        with conn.cursor() as cur:
            risultati = execute_values(
                cur, UPSERT_SQL, rows, template=ROW_TEMPLATE, page_size=PAGE_SIZE, fetch=True,
            )
        inserite = sum(1 for r in risultati if r[0])
        aggiornate = len(risultati) - inserite

    conn.commit()
    stato_run = "ok" if (inserite or aggiornate) else "nessuna_modifica"
    log_run(conn, fonte_id, stato_run, inserite=inserite, aggiornate=aggiornate, totali=totali)
    conn.commit()
    print(f"[bologna] totali={totali} inserite={inserite} aggiornate={aggiornate}")

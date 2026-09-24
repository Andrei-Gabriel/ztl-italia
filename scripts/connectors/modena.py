"""Connettore per il dataset ufficiale 'Zone a traffico limitato' del
Comune di Modena, erogato tramite il servizio WFS del Geoportale comunale
(GeoServer).

Fonte: https://opendata.comune.modena.it/dataset/ztl
Livello: Modena:Ztl_preview
Formato: GeoJSON via WFS (outputformat=json), richiesto già in WGS84
(srsName=EPSG:4326).

Verificato contro il dato reale: un'unica zona (Modena ha una sola ZTL, sul
centro storico), geometry già di tipo MultiPolygon (non serve ST_Multi),
properties con il campo "ENTITY" come identificativo/nome.
"""
import json

import requests
from psycopg2.extras import execute_values

from scripts.common import content_hash, get_or_create_fonte, log_run

WFS_URL = (
    "https://geonext.comune.modena.it/geoservernext//Modena/Ztl_preview/wfs"
    "?service=WFS&version=1.3.0&request=GetFeature"
    "&typename=Modena:Ztl_preview&outputformat=json&srsName=EPSG:4326"
)
COMUNE_ISTAT = "036023"  # Modena
PAGE_SIZE = 500

UPSERT_SQL = """
insert into ztl_zone
    (comune_istat, fonte_id, external_id, nome, tipo, geom, hash_contenuto)
values %s
on conflict (fonte_id, external_id) do update set
    nome = excluded.nome,
    geom = excluded.geom,
    hash_contenuto = excluded.hash_contenuto,
    aggiornato_il = now()
where ztl_zone.hash_contenuto is distinct from excluded.hash_contenuto
returning (xmax = 0) as inserted
"""
ROW_TEMPLATE = "(%s, %s, %s, %s, 'ZTL', ST_Multi(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))), %s)"


def fetch():
    resp = requests.get(WFS_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()


def run(conn):
    fonte_id = get_or_create_fonte(
        conn,
        comune_istat=COMUNE_ISTAT,
        nome_fonte="Comune di Modena - Geoportale (WFS, livello Ztl_preview)",
        tipo="ufficiale_comunale",
        url=WFS_URL,
        formato="wfs/geojson",
        licenza="CC BY 4.0",
    )

    try:
        data = fetch()
    except requests.RequestException as exc:
        print(f"[modena] ERRORE: {exc}")
        log_run(conn, fonte_id, "errore", messaggio=str(exc))
        conn.commit()
        return

    rows = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        geom = feature.get("geometry")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue  # scarta geometrie inattese, non blocca l'intero run

        external_id = feature.get("id") or str(props.get("ENTITY"))
        nome = props.get("ENTITY") or "ZTL Modena"

        h = content_hash(nome, geom)
        rows.append((COMUNE_ISTAT, fonte_id, external_id, nome, json.dumps(geom), h))

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
    stato = "ok" if (inserite or aggiornate) else "nessuna_modifica"
    log_run(conn, fonte_id, stato, inserite=inserite, aggiornate=aggiornate, totali=totali)
    conn.commit()
    print(f"[modena] totali={totali} inserite={inserite} aggiornate={aggiornate}")

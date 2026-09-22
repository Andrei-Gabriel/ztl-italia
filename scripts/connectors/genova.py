"""Connettore per il livello ufficiale 'ZTL' del Comune di Genova, erogato
tramite il servizio WFS del Geoportale comunale (GeoServer).

Fonte: https://mappe.comune.genova.it/geoserver/wfs
Livello: SITGEO:V_MOB_ZTL
Formato: GeoJSON via WFS (outputFormat=json), richiesto già in WGS84
(srsName=EPSG:4326) così il servizio fa lui la riproiezione da Gauss Boaga,
evitando di dover gestire la conversione di sistema di coordinate a mano.

Verificato contro il dato reale: properties (ID, NOME_ZONA, NOME_SOTTOZONA,
CONTR_ELETTR) e geometry di tipo Polygon (serve ST_Multi).
"""
import json

import requests
from psycopg2.extras import execute_values

from scripts.common import content_hash, get_or_create_fonte, log_run

WFS_URL = (
    "https://mappe.comune.genova.it/geoserver/wfs"
    "?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=SITGEO:V_MOB_ZTL&outputFormat=json&srsName=EPSG:4326"
)
COMUNE_ISTAT = "010025"  # Genova
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
ROW_TEMPLATE = "(%s, %s, %s, %s, 'ZTL', ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s)"


def fetch():
    resp = requests.get(WFS_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()


def run(conn):
    fonte_id = get_or_create_fonte(
        conn,
        comune_istat=COMUNE_ISTAT,
        nome_fonte="Comune di Genova - Geoportale (WFS, livello V_MOB_ZTL)",
        tipo="ufficiale_comunale",
        url=WFS_URL,
        formato="wfs/geojson",
        licenza="da verificare sul Geoportale",
    )

    try:
        data = fetch()
    except requests.RequestException as exc:
        print(f"[genova] ERRORE: {exc}")
        log_run(conn, fonte_id, "errore", messaggio=str(exc))
        conn.commit()
        return

    rows = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        geom = feature.get("geometry")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue  # scarta geometrie inattese, non blocca l'intero run

        external_id = str(props.get("ID"))
        zona = props.get("NOME_ZONA") or ""
        sottozona = props.get("NOME_SOTTOZONA") or ""
        nome = f"{zona} - {sottozona}".strip(" -") or zona or "ZTL Genova"
        controllo = props.get("CONTR_ELETTR")

        h = content_hash(nome, controllo, geom)
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
    print(f"[genova] totali={totali} inserite={inserite} aggiornate={aggiornate}")

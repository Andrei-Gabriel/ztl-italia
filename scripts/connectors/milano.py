"""Connettore per il dataset ufficiale 'Aree pedonali e ZTL' del Comune di Milano.
 
Fonte: https://dati.comune.milano.it/dataset/ds51_trafficotrasporti_aree_pedonali_ztl
Formato: GeoJSON, licenza CC-BY-4.0.
 
Verificato contro il dataset reale: ogni feature ha properties
(id_amat, tipo, nome, ordinanza, deroghe, val_inizio, val_fine, delibera,
tratto, note, cartello, dissuasori) e geometry di tipo MultiPolygon.
 
'tipo' assume i valori: AP (area pedonale), AREA_B, AREA_C, CR (carreggiata
riservata), ZTL (zona a traffico limitato in senso stretto).
"""
import json
 
import requests
from psycopg2.extras import execute_values
 
from scripts.common import content_hash, get_or_create_fonte, log_run
 
GEOJSON_URL = (
    "https://dati.comune.milano.it/dataset/d7d9179a-8228-427f-9c86-1e30154be4fe/"
    "resource/d788b26b-31b4-4e57-992d-276a1280c8c2/download/disciplina_aree.geojson"
)
COMUNE_ISTAT = "015146"  # Milano
PAGE_SIZE = 500
 
# Ordine delle colonne coerente col template SQL più sotto: la geometria
# viene passata come JSON grezzo e convertita lato database con
# ST_GeomFromGeoJSON, quindi non può stare in una VALUES %s "piatta" —
# serve un template esplicito riga per riga.
UPSERT_SQL = """
insert into ztl_zone
    (comune_istat, fonte_id, external_id, nome, tipo, deroghe,
     ordinanza_rif, geom, valido_da, valido_a, hash_contenuto)
values %s
on conflict (fonte_id, external_id) do update set
    nome = excluded.nome,
    tipo = excluded.tipo,
    deroghe = excluded.deroghe,
    ordinanza_rif = excluded.ordinanza_rif,
    geom = excluded.geom,
    valido_da = excluded.valido_da,
    valido_a = excluded.valido_a,
    hash_contenuto = excluded.hash_contenuto,
    aggiornato_il = now()
where ztl_zone.hash_contenuto is distinct from excluded.hash_contenuto
returning (xmax = 0) as inserted
"""
ROW_TEMPLATE = "(%s, %s, %s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s, %s, %s)"
 
 
def fetch():
    resp = requests.get(GEOJSON_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()
 
 
def run(conn):
    fonte_id = get_or_create_fonte(
        conn,
        comune_istat=COMUNE_ISTAT,
        nome_fonte="Comune di Milano - Open Data (DS51)",
        tipo="ufficiale_comunale",
        url=GEOJSON_URL,
        formato="geojson",
        licenza="CC-BY-4.0",
    )
 
    try:
        data = fetch()
    except requests.RequestException as exc:
        log_run(conn, fonte_id, "errore", messaggio=str(exc))
        conn.commit()
        return
 
    rows = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        geom = feature.get("geometry")
        if not geom or geom.get("type") != "MultiPolygon":
            continue  # scarta geometrie inattese, non blocca l'intero run
 
        external_id = str(props.get("id_amat"))
        nome = props.get("nome")
        tipo = props.get("tipo")
        deroghe = props.get("deroghe")
        ordinanza = props.get("ordinanza")
        val_inizio = props.get("val_inizio")
        val_fine = props.get("val_fine")
        h = content_hash(nome, tipo, deroghe, ordinanza, val_inizio, val_fine, geom)
 
        rows.append((
            COMUNE_ISTAT, fonte_id, external_id, nome, tipo, deroghe,
            ordinanza, json.dumps(geom), val_inizio, val_fine, h,
        ))
 
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
    print(f"[milano] totali={totali} inserite={inserite} aggiornate={aggiornate}")

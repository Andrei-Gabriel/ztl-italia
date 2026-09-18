"""Connettore per il dataset ufficiale 'ZTL diurna' del Comune di Firenze.
 
Fonte: https://opendata.comune.fi.it/index.php/page_dataset_show?id=9257a75e-3af3-4845-b37d-1540be29115a
Formato: GeoJSON, licenza CC-BY-4.0.
 
Verificato contro il dataset reale: ogni feature ha properties
(id, tipologia, atto_iniz) e geometry di tipo Polygon (non MultiPolygon come
Milano, per questo la query usa ST_Multi per convertirla). Solo 3 zone
(settori A, B, O della ZTL diurna).
 
LIMITAZIONE NOTA: il Comune di Firenze pubblica anche una 'ZTL notturna'
(estiva, settori F e G aggiuntivi), ma al momento della scrittura di questo
connettore era disponibile solo in formato SHP/KML, non GeoJSON — non è
inclusa qui per evitare di costruire un URL non verificato. Andrebbe
aggiunta con un connettore separato se/quando pubblicata anche in GeoJSON.
 
Non ci sono orari come dato strutturato in questo dataset (a differenza di
quanto ci si aspetterebbe): il campo orari resta vuoto. Gli orari generali
sono pubblici (es. lun-ven 7:30-20:00, sab 7:30-16:00) ma cambiano per
ordinanza e non è affidabile codificarli qui a mano.
"""
import json
 
import requests
from psycopg2.extras import execute_values
 
from scripts.common import content_hash, get_or_create_fonte, log_run
 
GEOJSON_URL = "https://datigis.comune.fi.it/json/ztl_diurna.json"
COMUNE_ISTAT = "048017"  # Firenze
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
# ST_Multi converte Polygon -> MultiPolygon (necessario perché la colonna
# geom è tipizzata MultiPolygon, mentre questa fonte usa Polygon semplice).
ROW_TEMPLATE = "(%s, %s, %s, %s, %s, %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s)"
 
 
def fetch():
    resp = requests.get(GEOJSON_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()
 
 
def run(conn):
    fonte_id = get_or_create_fonte(
        conn,
        comune_istat=COMUNE_ISTAT,
        nome_fonte="Comune di Firenze - Open Data (ZTL diurna)",
        tipo="ufficiale_comunale",
        url=GEOJSON_URL,
        formato="geojson",
        licenza="CC-BY-4.0",
    )
 
    try:
        data = fetch()
    except requests.RequestException as exc:
        print(f"[firenze] ERRORE: {exc}")
        log_run(conn, fonte_id, "errore", messaggio=str(exc))
        conn.commit()
        return
 
    rows = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        geom = feature.get("geometry")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue  # scarta geometrie inattese, non blocca l'intero run
 
        external_id = str(props.get("id"))
        tipologia = props.get("tipologia")
        atto_iniz = props.get("atto_iniz")
        nome = f"ZTL diurna - settore {tipologia}" if tipologia else "ZTL diurna"
 
        h = content_hash(nome, tipologia, atto_iniz, geom)
        rows.append((COMUNE_ISTAT, fonte_id, external_id, nome, tipologia, atto_iniz, json.dumps(geom), h))
 
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
    print(f"[firenze] totali={totali} inserite={inserite} aggiornate={aggiornate}")

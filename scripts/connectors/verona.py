"""Connettore per il dataset ufficiale 'Mappa della ZTL con varchi' del
Comune di Verona.

Fonte: https://www.comune.verona.it/Amministrazione/Documenti-e-dati/Dataset/Mappa-della-ZTL-con-varchi
Formato: CSV, licenza CC BY-NC 4.0.

LIMITAZIONE NOTA (importante): questo dataset contiene SOLO i varchi
(punti di accesso), non il perimetro della ZTL. Non c'è quindi nessuna riga
scritta in ztl_zone per Verona — solo in ztl_varchi, con zona_id lasciato
NULL (il campo è nullable apposta per questo caso). Se in futuro il Comune
pubblicasse anche il perimetro, andrebbe aggiunto un secondo connettore (o
esteso questo) per popolare anche ztl_zone.

Colonne CSV verificate: Varco, numero, Indirizzo, "Latitudine e longitudine"
(quest'ultima come stringa "lat,lon" dentro un unico campo).
"""
import csv
import io

import requests
from psycopg2.extras import execute_values

from scripts.common import content_hash, get_or_create_fonte, log_run

CSV_URL = "https://www.comune.verona.it/customexport/dataset-csv_resource-10484"
COMUNE_ISTAT = "023091"  # Verona
PAGE_SIZE = 500


def fetch():
    resp = requests.get(CSV_URL, timeout=60)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def run(conn):
    fonte_id = get_or_create_fonte(
        conn,
        comune_istat=COMUNE_ISTAT,
        nome_fonte="Comune di Verona - Open Data (Mappa ZTL con varchi)",
        tipo="ufficiale_comunale",
        url=CSV_URL,
        formato="csv",
        licenza="CC BY-NC 4.0",
    )

    try:
        testo_csv = fetch()
    except requests.RequestException as exc:
        print(f"[verona] ERRORE: {exc}")
        log_run(conn, fonte_id, "errore", messaggio=str(exc))
        conn.commit()
        return

    rows = []
    reader = csv.DictReader(io.StringIO(testo_csv))
    for r in reader:
        numero = (r.get("numero") or "").strip()
        nome = (r.get("Varco") or "").strip()
        indirizzo = (r.get("Indirizzo") or "").strip()
        lat_lon = (r.get("Latitudine e longitudine") or "").strip()
        if not numero or "," not in lat_lon:
            continue

        lat_str, lon_str = [p.strip() for p in lat_lon.split(",", 1)]
        try:
            lat, lon = float(lat_str), float(lon_str)
        except ValueError:
            continue

        external_id = numero
        h = content_hash(nome, indirizzo, lat, lon)
        point_geojson = f'{{"type":"Point","coordinates":[{lon},{lat}]}}'
        rows.append((None, COMUNE_ISTAT, fonte_id, external_id, nome, indirizzo, point_geojson, h))

    totali = len(rows)
    inserite = aggiornate = 0

    if rows:
        with conn.cursor() as cur:
            risultati = execute_values(
                cur,
                """
                insert into ztl_varchi
                    (zona_id, comune_istat, fonte_id, external_id, nome, note, geom, hash_contenuto)
                values %s
                on conflict (fonte_id, external_id) do update set
                    nome = excluded.nome,
                    note = excluded.note,
                    geom = excluded.geom,
                    hash_contenuto = excluded.hash_contenuto,
                    aggiornato_il = now()
                where ztl_varchi.hash_contenuto is distinct from excluded.hash_contenuto
                returning (xmax = 0) as inserted
                """,
                rows,
                template="(%s, %s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)",
                page_size=PAGE_SIZE,
                fetch=True,
            )
        inserite = sum(1 for r in risultati if r[0])
        aggiornate = len(risultati) - inserite

    conn.commit()
    stato = "ok" if (inserite or aggiornate) else "nessuna_modifica"
    log_run(conn, fonte_id, stato, inserite=inserite, aggiornate=aggiornate, totali=totali)
    conn.commit()
    print(f"[verona] varchi: totali={totali} inserite={inserite} aggiornate={aggiornate}")

"""Arricchisce la tabella `comuni`, già popolata da load_comuni.py, con
lat/lon del municipio di ciascun comune.

Fonte: opendatasicilia/comuni-italiani, file coordinate.csv (WGS84).
https://github.com/opendatasicilia/comuni-italiani

NOTA: in questo CSV il codice ISTAT (pro_com_t) è privo degli zeri iniziali
(es. "1001" invece di "001001"), quindi va rimesso a 6 cifre prima di fare
il confronto con comuni.istat_code.

Uso:
    export DATABASE_URL="postgresql://...supabase..."
    python -m scripts.enrich_lat_lon
"""
import csv
import io

import requests
from psycopg2.extras import execute_values

from scripts.common import get_db_connection

COORDINATE_CSV_URL = "https://raw.githubusercontent.com/opendatasicilia/comuni-italiani/main/dati/coordinate.csv"
PAGE_SIZE = 500


def fetch_coordinate():
    resp = requests.get(COORDINATE_CSV_URL, timeout=60)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = []
    for r in reader:
        istat_code = r["pro_com_t"].strip().zfill(6)
        rows.append((istat_code, float(r["lat"]), float(r["long"])))
    return rows


def run():
    rows = fetch_coordinate()
    print(f"Scaricate coordinate per {len(rows)} comuni.")

    conn = get_db_connection()
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            update comuni as c
            set lat = v.lat, lon = v.lon
            from (values %s) as v(istat_code, lat, lon)
            where c.istat_code = v.istat_code
            """,
            rows,
            page_size=PAGE_SIZE,
        )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("select count(*) from comuni where lat is not null")
        con_coordinate = cur.fetchone()[0]
        cur.execute("select count(*) from comuni")
        totale = cur.fetchone()[0]

    conn.close()
    print(f"Fatto. Comuni con lat/lon: {con_coordinate}/{totale}")


if __name__ == "__main__":
    run()

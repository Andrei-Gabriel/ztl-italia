"""Step 0 — Carica la tabella `comuni` con l'anagrafica di tutti i comuni
italiani (7904 comuni).
 
Fonte: matteocontrini/comuni-json (basata su dati ISTAT, licenza CC BY 3.0 IT).
https://github.com/matteocontrini/comuni-json
 
NOTA: questa fonte non include lat/lon del capoluogo. Il campo resta NULL
per ora: serve solo per l'abbinamento approssimato dei dati OSM (step 3), non
blocca il resto della pipeline. Si può arricchire in un secondo momento
(es. con un batch di geocoding) senza modificare lo schema.
 
Uso:
    export DATABASE_URL="postgresql://...supabase..."
    python -m scripts.load_comuni
"""
import requests
from psycopg2.extras import execute_values
 
from scripts.common import get_db_connection
 
COMUNI_JSON_URL = "https://raw.githubusercontent.com/matteocontrini/comuni-json/master/comuni.json"
 
# Quante righe per singolo giro di rete verso il database. 500-1000 è un buon
# compromesso: pochi round-trip, senza costruire una query gigantesca.
PAGE_SIZE = 500
 
 
def fetch_comuni():
    resp = requests.get(COMUNI_JSON_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()
 
 
def run():
    comuni = fetch_comuni()
    print(f"Scaricati {len(comuni)} comuni da comuni-json.")
 
    rows = [
        (
            c["codice"],
            c["nome"],
            c["provincia"]["nome"] or None,
            c["regione"]["nome"],
            c.get("popolazione"),
        )
        for c in comuni
    ]
 
    conn = get_db_connection()
    with conn.cursor() as cur:
        risultati = execute_values(
            cur,
            """
            insert into comuni (istat_code, nome, provincia, regione, popolazione)
            values %s
            on conflict (istat_code) do update set
                nome = excluded.nome,
                provincia = excluded.provincia,
                regione = excluded.regione,
                popolazione = excluded.popolazione
            returning (xmax = 0) as inserted
            """,
            rows,
            page_size=PAGE_SIZE,
            fetch=True,
        )
 
    conn.commit()
    conn.close()
 
    inserite = sum(1 for r in risultati if r[0])
    aggiornate = len(risultati) - inserite
    print(f"Fatto. Inserite: {inserite}, aggiornate: {aggiornate}, totale: {len(rows)}")
 
 
if __name__ == "__main__":
    run()

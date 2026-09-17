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

from scripts.common import get_db_connection

COMUNI_JSON_URL = "https://raw.githubusercontent.com/matteocontrini/comuni-json/master/comuni.json"


def fetch_comuni():
    resp = requests.get(COMUNI_JSON_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()


def run():
    comuni = fetch_comuni()
    print(f"Scaricati {len(comuni)} comuni da comuni-json.")

    conn = get_db_connection()
    inserite = 0
    aggiornate = 0

    with conn.cursor() as cur:
        for c in comuni:
            cur.execute(
                """
                insert into comuni (istat_code, nome, provincia, regione, popolazione)
                values (%s, %s, %s, %s, %s)
                on conflict (istat_code) do update set
                    nome = excluded.nome,
                    provincia = excluded.provincia,
                    regione = excluded.regione,
                    popolazione = excluded.popolazione
                returning (xmax = 0) as inserted
                """,
                (
                    c["codice"],
                    c["nome"],
                    c["provincia"]["nome"] or None,
                    c["regione"]["nome"],
                    c.get("popolazione"),
                ),
            )
            if cur.fetchone()[0]:
                inserite += 1
            else:
                aggiornate += 1

    conn.commit()
    conn.close()
    print(f"Fatto. Inserite: {inserite}, aggiornate: {aggiornate}, totale: {len(comuni)}")


if __name__ == "__main__":
    run()

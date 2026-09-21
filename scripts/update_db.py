"""Orchestratore principale — eseguito una volta a settimana da GitHub Actions
(vedi .github/workflows/weekly_update.yml).
 
Esegue, in ordine, tutti i connettori registrati. L'ordine ha importanza:
i connettori ufficiali comunali vanno DOPO OSM, così arricchiscono/prevalgono
sui dati OSM per lo stesso comune quando disponibili (per ora sono comunque
tenuti come righe separate in ztl_zone, distinte per fonte_id — vedi README
per la nota su come si evolverà la deduplicazione in step successivi).
 
Uso:
    export DATABASE_URL="postgresql://...supabase..."
    python -m scripts.update_db
"""
from scripts.common import get_db_connection
from scripts.connectors import firenze, milano, osm, torino, bologna, verona
 
# Aggiungere qui nuovi connettori comunali man mano che vengono scritti,
# seguendo lo stesso pattern di scripts/connectors/milano.py
CONNETTORI = [
    ("osm", osm.run),
    ("milano", milano.run),
    ("firenze", firenze.run),
    ("torino", torino.run),
    ("bologna", bologna.run),
    ("verona", verona.run),
]
 
 
def main():
    conn = get_db_connection()
    print(f"Avvio job settimanale — {len(CONNETTORI)} connettori da eseguire.")
 
    for nome, run_fn in CONNETTORI:
        print(f"--- Connettore: {nome} ---")
        try:
            run_fn(conn)
        except Exception as exc:  # non blocca gli altri connettori
            print(f"[{nome}] ERRORE non gestito: {exc}")
            conn.rollback()
 
    conn.close()
    print("Job settimanale completato.")
 
 
if __name__ == "__main__":
    main()

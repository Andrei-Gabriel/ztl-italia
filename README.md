# ZTL Italia — database centralizzato delle Zone a Traffico Limitato

Aggrega le ZTL dei comuni italiani in un database Postgres/PostGIS, aggiornato
automaticamente una volta a settimana tramite GitHub Actions. Nessun server
da gestire: tutto gira su infrastruttura gratuita (Supabase + GitHub Actions).

## Come è fatto

- **OpenStreetMap** (`scripts/connectors/osm.py`): fonte a copertura nazionale
  (~350 comuni mappati), tag `boundary=limited_traffic_zone`.
- **Fonti ufficiali comunali** (`scripts/connectors/milano.py` e altre da
  aggiungere): dati più ricchi (orari, deroghe, ordinanze) dove il comune li
  pubblica in open data.
- **`scripts/load_comuni.py`**: popola l'anagrafica dei comuni italiani
  (prerequisito, eseguito una volta).
- **`scripts/update_db.py`**: orchestratore eseguito ogni settimana.


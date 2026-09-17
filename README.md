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

## Setup — parte 1: il database (Supabase, gratuito)

1. Vai su **https://supabase.com** e crea un account (puoi usare GitHub per
   accedere).
2. Clicca **New Project**. Scegli un nome (es. `ztl-italia`), una password
   per il database (salvala, ti servirà) e una regione vicina (es. Frankfurt).
   Il piano **Free** va benissimo.
3. Attendi 1-2 minuti che il progetto venga creato.
4. Vai su **Database > Extensions** (menu a sinistra), cerca `postgis` e
   attivalo (basta un click).
5. Vai su **SQL Editor** (menu a sinistra), clicca **New query**, incolla
   tutto il contenuto del file `sql/schema.sql` di questo progetto, ed
   esegui (▶ Run). Dovresti vedere "Success. No rows returned".
6. Vai su **Project Settings > Database**, sezione **Connection string**.
   Scegli **Session pooler** (più compatibile con ambienti come GitHub
   Actions) e copia la stringa, sostituendo `[YOUR-PASSWORD]` con la password
   scelta al punto 2. Ha questa forma:
   ```
   postgresql://postgres.xxxxxxxxxxxx:LA-TUA-PASSWORD@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
   ```
   Questa è la tua `DATABASE_URL`. Tienila da parte, serve nei prossimi passi.

## Setup — parte 2: il codice (GitHub)

1. Crea un account su **https://github.com** se non ce l'hai già.
2. Crea un nuovo repository (può essere privato), es. `ztl-italia`.
3. Carica tutti i file di questo progetto nel repository (via web upload,
   oppure con `git` da terminale se preferisci — chiedimi pure se vuoi la
   guida anche per questo).
4. Nel repository, vai su **Settings > Secrets and variables > Actions**.
5. Clicca **New repository secret**. Nome: `DATABASE_URL`. Valore: la
   stringa di connessione copiata da Supabase al passo precedente. Salva.

A questo punto l'infrastruttura è pronta. Il job settimanale è già
configurato in `.github/workflows/weekly_update.yml` e partirà da solo ogni
lunedì — ma puoi anche avviarlo manualmente per il primo test (vedi sotto).

## Eseguire lo step 0 (caricare l'anagrafica comuni)

Va fatto una volta sola, prima del primo aggiornamento ZTL. Due modi:

**A. In locale**, se hai Python 3.12 installato:
```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql://...la tua stringa..."
python -m scripts.load_comuni
```

**B. Da GitHub Actions**, senza installare nulla in locale: dimmelo e ti
preparo un secondo workflow (`load_comuni.yml`) avviabile a mano da GitHub
con un click, così non serve toccare la riga di comando.

## Primo test del job settimanale

Dopo aver caricato i comuni, vai nel repository su GitHub, tab **Actions**,
seleziona **Aggiornamento settimanale ZTL** sulla sinistra, e clicca
**Run workflow** per lanciarlo subito senza aspettare lunedì. Puoi seguire i
log in tempo reale.

## Limitazioni note (v1)

- OSM copre solo i comuni già mappati dalla community (~350). Non c'è modo
  di "forzare" una copertura più ampia da OSM: per i comuni mancanti serve
  un connettore ufficiale dedicato (vedi sotto).
- L'abbinamento OSM → comune è approssimato (nearest-neighbor), perché la
  tabella `comuni` non ha ancora lat/lon precise. Non blocca l'uso, ma va
  tenuto presente per query molto puntuali.
- Solo Milano ha un connettore ufficiale per ora. Aggiungerne altri (Firenze,
  Torino, Cremona, ecc.) segue lo stesso pattern di `scripts/connectors/milano.py`
  — un connettore per volta, quando vuoi procedere.

## Prossimi passi possibili

- Aggiungere altri connettori comunali ufficiali.
- Backfill di lat/lon nella tabella `comuni` per un abbinamento OSM preciso.
- Notifica (email/Slack) se una fonte fallisce per più settimane di fila.
- Esposizione dei dati (API, export, dashboard) — solo quando il database
  si è popolato con continuità.

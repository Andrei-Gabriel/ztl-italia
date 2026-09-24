"""Connettore per i dataset ufficiali dei varchi ZTL di Roma Capitale,
erogati da Roma Servizi per la Mobilità s.r.l. su portale ArcGIS Hub.

Fonte: https://romamobilita.it/sistemi-e-tecnologie/dataset-geografici/
Portale: https://data-rsm.opendata.arcgis.com

LIMITAZIONE NOTA (importante): come Verona, questi dataset contengono SOLO
i varchi (punti di accesso), non il perimetro della ZTL — quello risultava
essere, nei dataset controllati, un export CAD grezzo non utilizzabile
(punti sparsi di un disegno tecnico, non un poligono). Nessuna riga viene
quindi scritta in ztl_zone, solo in ztl_varchi, con zona_id NULL.

A Roma non esiste un unico dataset con tutte le ZTL: ogni zona (Tridente,
Trastevere, Testaccio, San Lorenzo, Centro storico...) è un dataset ArcGIS
separato, VERIFICATO SINGOLARMENTE uno per uno contro il dato reale prima
di essere incluso qui. Ogni dataset usa nomi di campo diversi per la stessa
informazione (es. la via del varco si chiama "LOCALIZZAZ" in alcuni
dataset e "Localizz" in un altro) — da qui la configurazione esplicita per
zona invece di un parsing generico che assumerebbe uno schema comune.

Zone NON incluse perché non verificate con successo: AF1 VAM (ZTL B bus
turistici) — il primo tentativo di collegarla ha restituito un errore
"datasets of type Web Map are not supported", segno che l'ID trovato
puntava a una mappa interattiva e non al dataset scaricabile. Andrebbe
ripreso trovando l'ID del dataset corretto, con lo stesso procedimento di
verifica usato per le altre 5 zone.
"""
import json

import requests
from psycopg2.extras import execute_values

from scripts.common import content_hash, get_or_create_fonte, log_run

COMUNE_ISTAT = "058091"  # Roma
PAGE_SIZE = 500

DOWNLOAD_URL_TEMPLATE = (
    "https://hub.arcgis.com/api/v3/datasets/{item_id}_0/downloads/data"
    "?format=geojson&spatialRefId=4326"
)

# Un dataset ArcGIS separato per zona. nome_field/extra_field/orario_field
# sono i nomi dei campi VERIFICATI in QUEL dataset specifico (confermati
# uno per uno, non assunti).
DATASETS = [
    {
        "chiave": "tridente",
        "zona": "Tridente",
        "item_id": "29ca4c5a6abc4b69bdf7c33fc47630ed",
        "nome_field": "NOMEIMMAGI",
        "extra_field": None,
        "orario_field": None,
    },
    {
        "chiave": "trastevere",
        "zona": "Trastevere",
        "item_id": "add35d66a0234c27a460ecd4d7248760",
        "nome_field": "LOCALIZZAZ",
        "extra_field": "N_VARCO",
        "orario_field": "Accensione",
    },
    {
        "chiave": "testaccio",
        "zona": "Testaccio",
        "item_id": "41037e0a9f06431ba39304fe42b3f371",
        "nome_field": "Localizz",
        "extra_field": "Presso",
        "orario_field": None,
    },
    {
        "chiave": "san_lorenzo",
        "zona": "San Lorenzo",
        "item_id": "0648434a587e4d17bc5fd6a26bc020c8",
        "nome_field": "LOCALIZZAZ",
        "extra_field": "RIFERIMENT",
        "orario_field": None,
    },
    {
        "chiave": "centro_storico",
        "zona": "Centro storico (diurna)",
        "item_id": "3a87172a1bed4e9dba20bd4c820dd226",
        "nome_field": "LOCALIZZAZ",
        "extra_field": "RIFERIMENT",
        "orario_field": None,
    },
]


def fetch(item_id):
    url = DOWNLOAD_URL_TEMPLATE.format(item_id=item_id)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def run(conn):
    fonte_id = get_or_create_fonte(
        conn,
        comune_istat=COMUNE_ISTAT,
        nome_fonte="Roma Servizi per la Mobilità - Open Data (ArcGIS Hub, varchi per zona)",
        tipo="ufficiale_comunale",
        url="https://data-rsm.opendata.arcgis.com/",
        formato="geojson (ArcGIS Hub)",
        licenza="da verificare sul portale ArcGIS Hub",
    )

    rows = []
    zone_ok = []
    zone_fallite = []

    for cfg in DATASETS:
        try:
            data = fetch(cfg["item_id"])
        except requests.RequestException as exc:
            print(f"[roma] zona {cfg['zona']} FALLITA: {exc}")
            zone_fallite.append(cfg["zona"])
            continue

        n_zona = 0
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            geom = feature.get("geometry")
            if not geom or geom.get("type") != "Point":
                continue  # scarta geometrie inattese, non blocca l'intero run

            fid = props.get("FID")
            if fid is None:
                continue

            def _testo(campo):
                if not campo:
                    return None
                valore = props.get(campo)
                if valore is None or valore == "":
                    return None
                return str(valore).strip() or None

            nome = _testo(cfg["nome_field"]) or f"Varco {cfg['zona']} #{fid}"
            extra = _testo(cfg["extra_field"])
            orario = _testo(cfg["orario_field"])
            nota = " / ".join(p for p in [cfg["zona"], extra, orario] if p) or cfg["zona"]

            external_id = f"{cfg['chiave']}_{fid}"
            h = content_hash(nome, nota, geom)
            rows.append((None, COMUNE_ISTAT, fonte_id, external_id, nome, nota, json.dumps(geom), h))
            n_zona += 1

        print(f"[roma] zona {cfg['zona']}: {n_zona} varchi letti")
        zone_ok.append(cfg["zona"])

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
    stato = "ok" if (inserite or aggiornate) else ("errore" if zone_fallite else "nessuna_modifica")
    messaggio = f"zone ok: {', '.join(zone_ok)} | zone fallite: {', '.join(zone_fallite)}" if zone_fallite else None
    log_run(conn, fonte_id, stato, messaggio=messaggio, inserite=inserite, aggiornate=aggiornate, totali=totali)
    conn.commit()
    print(
        f"[roma] varchi: totali={totali} inserite={inserite} aggiornate={aggiornate}"
        + (f" | zone fallite: {zone_fallite}" if zone_fallite else "")
    )

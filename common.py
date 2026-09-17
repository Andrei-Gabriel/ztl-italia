"""Funzioni condivise dai connettori e dall'orchestratore."""
import hashlib
import json
import os

import psycopg2


def get_db_connection():
    """Si connette al database usando la variabile d'ambiente DATABASE_URL.

    Su Supabase: Project Settings > Database > Connection string (usa quella
    'Session pooler' o 'Direct connection' con sslmode=require).
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "Variabile d'ambiente DATABASE_URL non impostata. "
            "In locale: export DATABASE_URL='postgresql://...'. "
            "In GitHub Actions: aggiungila come Secret del repository."
        )
    return psycopg2.connect(dsn)


def content_hash(*parts) -> str:
    """Hash stabile dei campi rilevanti di un record, per capire se è cambiato
    rispetto alla settimana precedente senza dover confrontare la geometria
    campo per campo."""
    raw = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_or_create_fonte(conn, comune_istat, nome_fonte, tipo, url, formato, licenza=None):
    """Ritorna l'id della riga in `fonti` corrispondente, creandola se assente."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select id from fonti
            where coalesce(comune_istat, '') = coalesce(%s, '') and nome_fonte = %s
            """,
            (comune_istat, nome_fonte),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            """
            insert into fonti (comune_istat, nome_fonte, tipo, url, formato, licenza)
            values (%s, %s, %s, %s, %s, %s)
            returning id
            """,
            (comune_istat, nome_fonte, tipo, url, formato, licenza),
        )
        return cur.fetchone()[0]


def log_run(conn, fonte_id, stato, messaggio="", inserite=0, aggiornate=0, totali=0):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into run_log (fonte_id, stato, messaggio, righe_inserite, righe_aggiornate, righe_totali)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (fonte_id, stato, messaggio, inserite, aggiornate, totali),
        )

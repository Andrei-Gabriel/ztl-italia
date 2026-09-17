-- ZTL Italia — schema database
-- Richiede l'estensione PostGIS (Supabase la abilita da Database > Extensions)

create extension if not exists postgis;

-- Anagrafica comuni (ISTAT)
create table if not exists comuni (
    istat_code      text primary key,
    nome            text not null,
    provincia       text,
    regione         text,
    popolazione     integer,
    lat             double precision,
    lon             double precision
);

-- Registro delle fonti dati collegate (una riga per comune+fonte)
create table if not exists fonti (
    id              bigint generated always as identity primary key,
    comune_istat    text references comuni(istat_code),
    nome_fonte      text not null,           -- es. 'Comune di Milano - Open Data', 'OpenStreetMap'
    tipo            text not null check (tipo in ('ufficiale_comunale','ufficiale_regionale','osm','altro')),
    url             text not null,
    formato         text,                    -- 'geojson','csv','wms','xml','overpass'
    licenza         text,
    attiva          boolean default true,
    ultimo_ok       timestamptz,
    ultimo_errore   timestamptz,
    note_errore     text
);

-- Zone ZTL (poligoni)
create table if not exists ztl_zone (
    id                  bigint generated always as identity primary key,
    comune_istat        text references comuni(istat_code),
    fonte_id            bigint references fonti(id),
    external_id         text,                -- id nel dataset di origine, per fare upsert idempotente
    nome                text,
    tipo                text,                -- ZTL / Area B / Area C / area pedonale / ecc.
    orari               text,                -- testo libero, es. "lun-ven 07:30-19:30"
    deroghe             text,                -- veicoli ammessi in deroga
    ordinanza_rif       text,
    geom                geometry(MultiPolygon, 4326) not null,
    valido_da           date,
    valido_a            date,
    hash_contenuto      text not null,       -- hash dei campi rilevanti, per rilevare modifiche settimana su settimana
    creato_il           timestamptz default now(),
    aggiornato_il       timestamptz default now(),
    unique (fonte_id, external_id)
);
create index if not exists idx_ztl_zone_geom on ztl_zone using gist (geom);
create index if not exists idx_ztl_zone_comune on ztl_zone (comune_istat);

-- Varchi/punti di accesso (quando la fonte li fornisce)
create table if not exists ztl_varchi (
    id                  bigint generated always as identity primary key,
    zona_id             bigint references ztl_zone(id) on delete set null,
    comune_istat        text references comuni(istat_code),
    fonte_id            bigint references fonti(id),
    external_id         text,
    nome                text,
    geom                geometry(Point, 4326) not null,
    note                text,
    hash_contenuto      text not null,
    creato_il           timestamptz default now(),
    aggiornato_il       timestamptz default now(),
    unique (fonte_id, external_id)
);
create index if not exists idx_ztl_varchi_geom on ztl_varchi using gist (geom);

-- Log di ogni esecuzione del job settimanale, per fonte
create table if not exists run_log (
    id              bigint generated always as identity primary key,
    eseguito_il     timestamptz default now(),
    fonte_id        bigint references fonti(id),
    stato           text check (stato in ('ok','errore','nessuna_modifica')),
    messaggio       text,
    righe_inserite  integer default 0,
    righe_aggiornate integer default 0,
    righe_totali    integer default 0
);

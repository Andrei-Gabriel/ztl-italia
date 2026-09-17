"""Connettore di fallback a copertura nazionale: interroga Overpass API per
tutti gli elementi OSM taggati boundary=limited_traffic_zone in Italia.

Questo è il tag standard usato dalla community OSM italiana per le ZTL
(vedi https://wiki.openstreetmap.org/wiki/IT:Tag:boundary=limited_traffic_zone),
con circa 350 città mappate. Copertura più ampia ma meno ricca di dettagli
(orari/deroghe spesso mancanti o in formato opening_hours) rispetto alle
fonti ufficiali comunali.

LIMITAZIONE NOTA: l'associazione al comune ISTAT è fatta per nearest-neighbor
sul centroide della geometria rispetto al capoluogo comunale (tabella
`comuni`), quindi è APPROSSIMATA. Per una associazione esatta servirebbe un
join spaziale poligono-in-poligono con i confini amministrativi ISTAT: è
segnalato come miglioramento futuro nel README.
"""
import json
import math

import requests

from scripts.common import content_hash, get_or_create_fonte, log_run

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOME_FONTE = "OpenStreetMap (boundary=limited_traffic_zone)"

QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="IT"][admin_level=2]->.italy;
(
  way["boundary"="limited_traffic_zone"](area.italy);
  relation["boundary"="limited_traffic_zone"](area.italy);
);
out body geom;
"""


def fetch():
    resp = requests.post(OVERPASS_URL, data={"data": QUERY}, timeout=200)
    resp.raise_for_status()
    return resp.json()


def _way_to_polygon_coords(geometry_points):
    coords = [[p["lon"], p["lat"]] for p in geometry_points]
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])  # chiude l'anello se necessario
    return coords


def _element_to_multipolygon(el):
    """Converte un elemento Overpass (way o relation con geometria) in
    GeoJSON MultiPolygon. Per le relation, tratta ogni way membro come un
    ring separato (semplificazione: non distingue inner/outer)."""
    if el["type"] == "way":
        geom = el.get("geometry")
        if not geom or len(geom) < 4:
            return None
        return {"type": "MultiPolygon", "coordinates": [[_way_to_polygon_coords(geom)]]}

    if el["type"] == "relation":
        polygons = []
        for member in el.get("members", []):
            geom = member.get("geometry")
            if geom and len(geom) >= 4:
                polygons.append([_way_to_polygon_coords(geom)])
        if not polygons:
            return None
        return {"type": "MultiPolygon", "coordinates": polygons}

    return None


def _centroid(multipolygon_coords):
    lons, lats = [], []
    for polygon in multipolygon_coords:
        for ring in polygon:
            for lon, lat in ring:
                lons.append(lon)
                lats.append(lat)
    if not lons:
        return None
    return sum(lons) / len(lons), sum(lats) / len(lats)


def _nearest_comune(conn, lon, lat):
    """Nearest-neighbor approssimato sul capoluogo comunale. Richiede che la
    tabella `comuni` sia già popolata con lat/lon (vedi README, step 0)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select istat_code, nome
            from comuni
            where lat is not null and lon is not null
            order by (lat - %s)^2 + (lon - %s)^2
            limit 1
            """,
            (lat, lon),
        )
        return cur.fetchone()


def run(conn):
    fonte_id = get_or_create_fonte(
        conn,
        comune_istat=None,
        nome_fonte=NOME_FONTE,
        tipo="osm",
        url=OVERPASS_URL,
        formato="overpass",
        licenza="ODbL",
    )

    try:
        data = fetch()
    except requests.RequestException as exc:
        log_run(conn, fonte_id, "errore", messaggio=str(exc))
        conn.commit()
        return

    inserite = 0
    aggiornate = 0
    totali = 0

    with conn.cursor() as cur:
        for el in data.get("elements", []):
            mp = _element_to_multipolygon(el)
            if not mp:
                continue

            centroid = _centroid(mp["coordinates"])
            comune = _nearest_comune(conn, *centroid) if centroid else None
            comune_istat = comune[0] if comune else None

            tags = el.get("tags", {})
            nome = tags.get("name")
            orari = tags.get("opening_hours")

            totali += 1
            external_id = f"osm_{el['type']}_{el['id']}"
            h = content_hash(nome, orari, mp)

            cur.execute(
                """
                insert into ztl_zone
                    (comune_istat, fonte_id, external_id, nome, tipo, orari, geom, hash_contenuto)
                values
                    (%s, %s, %s, %s, 'ZTL', %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)
                on conflict (fonte_id, external_id) do update set
                    comune_istat = excluded.comune_istat,
                    nome = excluded.nome,
                    orari = excluded.orari,
                    geom = excluded.geom,
                    hash_contenuto = excluded.hash_contenuto,
                    aggiornato_il = now()
                where ztl_zone.hash_contenuto is distinct from excluded.hash_contenuto
                returning (xmax = 0) as inserted
                """,
                (comune_istat, fonte_id, external_id, nome, orari, json.dumps(mp), h),
            )
            result = cur.fetchone()
            if result is not None:
                if result[0]:
                    inserite += 1
                else:
                    aggiornate += 1

    conn.commit()
    stato = "ok" if (inserite or aggiornate) else "nessuna_modifica"
    log_run(conn, fonte_id, stato, inserite=inserite, aggiornate=aggiornate, totali=totali)
    conn.commit()
    print(f"[osm] totali={totali} inserite={inserite} aggiornate={aggiornate}")

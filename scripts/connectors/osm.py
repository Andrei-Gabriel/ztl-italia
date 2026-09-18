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
 
import requests
from psycopg2.extras import execute_values
 
from scripts.common import content_hash, get_or_create_fonte, log_run
 
# Overpass a volte blocca/limita le richieste dagli IP condivisi dei runner
# GitHub Actions. Proviamo più mirror pubblici in ordine, il primo che
# risponde va bene. Se falliscono tutti, l'errore viene loggato per intero.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
NOME_FONTE = "OpenStreetMap (boundary=limited_traffic_zone)"
PAGE_SIZE = 500
 
# Un User-Agent descrittivo è richiesto dalla policy d'uso di Overpass;
# le richieste anonime/generiche vengono più facilmente bloccate.
HEADERS = {"User-Agent": "ztl-italia-project/1.0 (progetto personale, no contatto pubblico)"}
 
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
    ultimo_errore = None
    for url in OVERPASS_MIRRORS:
        try:
            print(f"[osm] provo mirror: {url}")
            resp = requests.post(url, data={"data": QUERY}, headers=HEADERS, timeout=200)
            resp.raise_for_status()
            print(f"[osm] mirror {url} ha risposto correttamente")
            return resp.json()
        except requests.RequestException as exc:
            print(f"[osm] mirror {url} fallito: {exc}")
            ultimo_errore = exc
    raise ultimo_errore
 
 
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
 
 
def _load_comuni_con_coordinate(conn):
    """Carica una sola volta in memoria i comuni che hanno lat/lon, invece di
    interrogare il database una volta per ogni ZTL (che con centinaia di
    elementi significherebbe centinaia di giri di rete separati).
 
    NOTA: la fonte usata in scripts/load_comuni.py non fornisce lat/lon, quindi
    finché non viene aggiunto un arricchimento dedicato questa lista sarà
    vuota e il campo comune_istat resterà NULL per le zone OSM — coerente con
    la limitazione già documentata nel README."""
    with conn.cursor() as cur:
        cur.execute("select istat_code, lat, lon from comuni where lat is not null and lon is not null")
        return cur.fetchall()
 
 
def _nearest_comune(comuni_con_coordinate, lon, lat):
    """Nearest-neighbor approssimato in Python (nessun giro di rete)."""
    if not comuni_con_coordinate:
        return None
    return min(
        comuni_con_coordinate,
        key=lambda c: (c[1] - lat) ** 2 + (c[2] - lon) ** 2,
    )
 
 
def run(conn):
    fonte_id = get_or_create_fonte(
        conn,
        comune_istat=None,
        nome_fonte=NOME_FONTE,
        tipo="osm",
        url=OVERPASS_MIRRORS[0],
        formato="overpass",
        licenza="ODbL",
    )
 
    try:
        data = fetch()
    except requests.RequestException as exc:
        print(f"[osm] ERRORE: tutti i mirror Overpass hanno fallito: {exc}")
        log_run(conn, fonte_id, "errore", messaggio=str(exc))
        conn.commit()
        return
 
    comuni_con_coordinate = _load_comuni_con_coordinate(conn)
    elementi_grezzi = data.get("elements", [])
    print(f"[osm] Overpass ha restituito {len(elementi_grezzi)} elementi grezzi (way+relation)")
 
    rows = []
    for el in elementi_grezzi:
        mp = _element_to_multipolygon(el)
        if not mp:
            continue
 
        centroid = _centroid(mp["coordinates"])
        comune = _nearest_comune(comuni_con_coordinate, *centroid) if centroid else None
        comune_istat = comune[0] if comune else None
 
        tags = el.get("tags", {})
        nome = tags.get("name")
        orari = tags.get("opening_hours")
 
        external_id = f"osm_{el['type']}_{el['id']}"
        h = content_hash(nome, orari, mp)
 
        rows.append((comune_istat, fonte_id, external_id, nome, orari, json.dumps(mp), h))
 
    totali = len(rows)
    inserite = aggiornate = 0
 
    if rows:
        with conn.cursor() as cur:
            risultati = execute_values(
                cur,
                """
                insert into ztl_zone
                    (comune_istat, fonte_id, external_id, nome, tipo, orari, geom, hash_contenuto)
                values %s
                on conflict (fonte_id, external_id) do update set
                    comune_istat = excluded.comune_istat,
                    nome = excluded.nome,
                    orari = excluded.orari,
                    geom = excluded.geom,
                    hash_contenuto = excluded.hash_contenuto,
                    aggiornato_il = now()
                where ztl_zone.hash_contenuto is distinct from excluded.hash_contenuto
                   or ztl_zone.comune_istat is distinct from excluded.comune_istat
                returning (xmax = 0) as inserted
                """,
                rows,
                template="(%s, %s, %s, %s, 'ZTL', %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)",
                page_size=PAGE_SIZE,
                fetch=True,
            )
        inserite = sum(1 for r in risultati if r[0])
        aggiornate = len(risultati) - inserite
 
    conn.commit()
    stato = "ok" if (inserite or aggiornate) else "nessuna_modifica"
    log_run(conn, fonte_id, stato, inserite=inserite, aggiornate=aggiornate, totali=totali)
    conn.commit()
    print(f"[osm] totali={totali} inserite={inserite} aggiornate={aggiornate}")

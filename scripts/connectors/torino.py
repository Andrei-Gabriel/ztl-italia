"""Connettore per il dataset ufficiale 'Perimetro, varchi e orari ZTL' della
Città di Torino, erogato da 5T S.r.l. (progetto SIMONE).

Fonte: https://aperto.comune.torino.it/dataset/perimetro-varchi-e-orari-ztl
URL dati: https://opendata.5t.torino.it/get_access_control
Formato: XML (schema access_control.xsd).

Verificato contro il dato reale: <ztl id name> contiene un <polyline> di
punti lat/lng (perimetro, già chiuso: primo punto = ultimo), un blocco
<gates> con i varchi (id, name, lat, lng, heading) e un <access_policy> con
gli orari (<time_policy>, con elementi weekday/day_before_holiday/holiday
che hanno start_time/end_time quando l'orario si applica).

A differenza delle altre fonti, questa fornisce sia orari strutturati sia i
varchi, quindi il connettore popola anche la tabella ztl_varchi.
"""
import json
import xml.etree.ElementTree as ET

import requests
from psycopg2.extras import execute_values

from scripts.common import content_hash, get_or_create_fonte, log_run

DATA_URL = "https://opendata.5t.torino.it/get_access_control"
COMUNE_ISTAT = "001272"  # Torino
PAGE_SIZE = 500

GIORNI_LABEL = {
    "weekday": "lun-ven",
    "day_before_holiday": "prefestivo",
    "holiday": "festivo",
    "day_of_week": "giorno specifico",
}


def fetch():
    resp = requests.get(DATA_URL, timeout=60)
    resp.raise_for_status()
    return resp.text


def _strip_ns(tag):
    """Rimuove il namespace XML (es. '{https://...}ztl' -> 'ztl') per non
    dover scrivere ogni ricerca con il namespace completo."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _parse(xml_text):
    root = ET.fromstring(xml_text)
    for el in root.iter():
        el.tag = _strip_ns(el.tag)
    return root


def _build_orari(access_policy_el):
    if access_policy_el is None:
        return None
    time_policy = access_policy_el.find("time_policy")
    if time_policy is None:
        return None
    parti = []
    for child in time_policy:
        start = child.get("start_time")
        end = child.get("end_time")
        if start and end:
            label = GIORNI_LABEL.get(child.tag, child.tag)
            parti.append(f"{label} {start[:5]}-{end[:5]}")
    return "; ".join(parti) if parti else None


def _polyline_to_polygon_coords(polyline_el):
    points = polyline_el.findall("point")
    coords = [[float(p.get("lng")), float(p.get("lat"))] for p in points]
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])  # chiude l'anello se necessario
    return coords


def run(conn):
    fonte_id = get_or_create_fonte(
        conn,
        comune_istat=COMUNE_ISTAT,
        nome_fonte="Città di Torino - Open Data (5T / progetto SIMONE)",
        tipo="ufficiale_comunale",
        url=DATA_URL,
        formato="xml",
        licenza="da verificare sul portale aperTO",
    )

    try:
        xml_text = fetch()
        root = _parse(xml_text)
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"[torino] ERRORE: {exc}")
        log_run(conn, fonte_id, "errore", messaggio=str(exc))
        conn.commit()
        return

    ztl_elements = root.findall("ztl")
    print(f"[torino] trovate {len(ztl_elements)} zone ZTL nel documento")

    zone_inserite = zone_aggiornate = 0
    varchi_totali = 0
    varchi_rows = []

    for ztl_el in ztl_elements:
        external_id = ztl_el.get("id")
        nome = ztl_el.get("name")

        polyline_el = ztl_el.find("polyline")
        if polyline_el is None:
            continue
        coords = _polyline_to_polygon_coords(polyline_el)
        if len(coords) < 4:
            continue

        orari = _build_orari(ztl_el.find("access_policy"))
        geom = {"type": "MultiPolygon", "coordinates": [[coords]]}
        h = content_hash(nome, orari, geom)

        # Upsert singolo (non in blocco): le zone Torino sono poche, e ci
        # serve subito l'id per collegare i varchi.
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into ztl_zone
                    (comune_istat, fonte_id, external_id, nome, orari, geom, hash_contenuto)
                values (%s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)
                on conflict (fonte_id, external_id) do update set
                    nome = excluded.nome,
                    orari = excluded.orari,
                    geom = excluded.geom,
                    hash_contenuto = excluded.hash_contenuto,
                    aggiornato_il = now()
                returning id, (xmax = 0) as inserted
                """,
                (COMUNE_ISTAT, fonte_id, external_id, nome, orari, json.dumps(geom), h),
            )
            zona_id, inserted = cur.fetchone()
        if inserted:
            zone_inserite += 1
        else:
            zone_aggiornate += 1

        gates_el = ztl_el.find("gates")
        if gates_el is None:
            continue
        for gate in gates_el.findall("gate"):
            gate_id = gate.get("id")
            gate_nome = gate.get("name")
            lat, lng = gate.get("lat"), gate.get("lng")
            if lat is None or lng is None:
                continue
            varchi_totali += 1
            gate_hash = content_hash(gate_nome, lat, lng, gate.get("heading"))
            point_geojson = f'{{"type":"Point","coordinates":[{lng},{lat}]}}'
            varchi_rows.append((
                zona_id, COMUNE_ISTAT, fonte_id, f"{external_id}_{gate_id}",
                gate_nome, point_geojson, gate_hash,
            ))

    varchi_inserite = varchi_aggiornate = 0
    if varchi_rows:
        with conn.cursor() as cur:
            risultati = execute_values(
                cur,
                """
                insert into ztl_varchi
                    (zona_id, comune_istat, fonte_id, external_id, nome, geom, hash_contenuto)
                values %s
                on conflict (fonte_id, external_id) do update set
                    zona_id = excluded.zona_id,
                    nome = excluded.nome,
                    geom = excluded.geom,
                    hash_contenuto = excluded.hash_contenuto,
                    aggiornato_il = now()
                where ztl_varchi.hash_contenuto is distinct from excluded.hash_contenuto
                returning (xmax = 0) as inserted
                """,
                varchi_rows,
                template="(%s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)",
                page_size=PAGE_SIZE,
                fetch=True,
            )
        varchi_inserite = sum(1 for r in risultati if r[0])
        varchi_aggiornate = len(risultati) - varchi_inserite

    conn.commit()
    stato = "ok" if (zone_inserite or zone_aggiornate) else "nessuna_modifica"
    log_run(
        conn, fonte_id, stato,
        inserite=zone_inserite, aggiornate=zone_aggiornate, totali=len(ztl_elements),
    )
    conn.commit()
    print(
        f"[torino] zone: totali={len(ztl_elements)} inserite={zone_inserite} aggiornate={zone_aggiornate} | "
        f"varchi: totali={varchi_totali} inserite={varchi_inserite} aggiornate={varchi_aggiornate}"
    )

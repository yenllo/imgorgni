"""
Fase 3: Agrupación de fotos en eventos, y de eventos en viajes.

Reemplaza al DBSCAN global de 3_agrupacion_mejorado.py, que era O(n^2) sobre
toda la fototeca (~37 h proyectadas para 150.000 fotos). Acá se aprovecha que
los eventos son contiguos en el tiempo: se parte primero por huecos temporales
y recién dentro de cada bloque se compara contenido. Eso baja el costo a
O(suma de bloques^2), que con bloques de decenas o cientos de fotos es trivial.

Señales usadas para decidir si dos fotos son del mismo evento:
  - tiempo      : hueco entre disparos (la señal más fuerte)
  - contenido   : similitud coseno del embedding
  - ubicación   : distancia GPS cuando ambas la tienen
  - cámara      : dos cámaras distintas en simultáneo suelen ser el mismo evento
  - personas    : eventos vecinos que comparten gente se fusionan

Después agrupa eventos cercanos en tiempo y lugar bajo un "viaje" (evento padre).

    python main.py -ph 3
"""
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

from db import (get_conn, init_db, fetch_features_with_time, create_event,
                assign_photos_to_event)
from utils_analisis import clasificar_dispositivo
from utils_log import Spinner, log_step

RADIO_TIERRA_KM = 6371.0


def _parse(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def distancia_km(lat1, lon1, lat2, lon2) -> float:
    """Haversine. Devuelve inf si a alguna le falta el dato."""
    if None in (lat1, lon1, lat2, lon2):
        return float("inf")
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * RADIO_TIERRA_KM * math.asin(math.sqrt(a))


def cargar_fotos(conn: sqlite3.Connection) -> List[Dict]:
    """Fotos analizadas a reagrupar, ordenadas por fecha.

    Quedan afuera las que ya pertenecen a un evento con nombre: ese evento se
    conserva tal cual, y si sus fotos igual entran al clustering terminan
    tambien dentro de un evento nuevo, o sea la misma foto en dos eventos.

    Y quedan afuera dos cosas mas, que antes entraban y ensuciaban todo:

      - Las de TELEFONO. El proyecto es de fotos de camara; ademas el
        clustering agrupa por contenido, asi que las del telefono se armaban
        sus propios eventos de 3 fotos ('bar', 'garden', 'plate of food').
        Al 13/8/2026 eran 2.417 de 3.111 eventos: el 78%.
      - Las BORRADAS (cuarentena). Seguian entrando al clustering y volviendo
        a aparecer dentro de eventos; por eso el curador tenia que limpiarlas
        en cada arranque (arreglar_cuarentena).

    Las 'desconocido' se quedan a proposito: no son de telefono, son exports
    web sin EXIF de material propio de camara.
    """
    filas = conn.execute("""
        SELECT f.id, f.capture_date, f.camera_model, f.gps_lat, f.gps_lon, f.place,
               pf.visual_embedding, pf.scene_tags, pf.tags_json, f.file_path
        FROM files f JOIN photo_features pf ON f.id = pf.photo_id
        WHERE f.capture_date IS NOT NULL
          AND COALESCE(f.borrado, 0) = 0
          AND f.id NOT IN (SELECT ep.photo_id FROM event_photos ep
                           JOIN events e ON e.id = ep.event_id
                           WHERE e.name IS NOT NULL)
    """).fetchall()

    fotos = []
    descartadas = 0
    for pid, fecha, camara, lat, lon, lugar, emb_json, escenas, tags, ruta in filas:
        if clasificar_dispositivo(camara, ruta or "") == "telefono":
            descartadas += 1
            continue
        dt = _parse(fecha)
        if dt is None:
            continue
        try:
            emb = np.asarray(json.loads(emb_json or "[]"), dtype=np.float32)
        except json.JSONDecodeError:
            continue
        if emb.size == 0:
            continue
        n = float(np.linalg.norm(emb))
        if n <= 1e-8:
            continue
        def _cargar(x):
            try:
                return json.loads(x or "[]")
            except json.JSONDecodeError:
                return []
        fotos.append({
            "id": pid, "dt": dt, "emb": emb / n,
            "camara": camara, "lat": lat, "lon": lon, "lugar": lugar,
            "escenas": _cargar(escenas), "tags": _cargar(tags),
        })
    fotos.sort(key=lambda f: f["dt"])
    if descartadas:
        print(f"  {descartadas:,} fotos de telefono fuera del clustering")
    return fotos


def personas_por_foto(conn: sqlite3.Connection) -> Dict[int, set]:
    """photo_id -> conjunto de person_id. Vacío si la Fase 3b no corrió."""
    salida = defaultdict(set)
    try:
        for pid, person in conn.execute(
                "SELECT photo_id, person_id FROM faces WHERE person_id >= 0"):
            salida[pid].add(person)
    except sqlite3.Error:
        pass
    return salida


def partir_por_tiempo(fotos: List[Dict], horas: float, adaptativo: bool = True,
                      k: float = 1.8, ventana: int = 11) -> List[List[Dict]]:
    """
    Corta la secuencia donde el hueco temporal indica cambio de evento.

    Un umbral fijo falla en los dos extremos: en una sesión de fotos cada 10
    segundos, 4 h no corta nunca; en fotos sueltas de vacaciones, corta de más.
    El criterio adaptativo compara cada hueco contra sus vecinos en escala
    logarítmica —lo que hace Cooper & Foote y PhotoTOC— y corta cuando se
    despega del ritmo local.

    Dos guardas duras acotan los modos de falla del adaptativo puro:
      - más de `horas` siempre corta (dormiste en el medio)
      - menos de 20 minutos nunca corta (es el mismo momento)
    """
    if not fotos:
        return []
    if len(fotos) < ventana or not adaptativo:
        cortes = [
            (f2["dt"] - f1["dt"]).total_seconds() / 3600.0 > horas
            for f1, f2 in zip(fotos, fotos[1:])
        ]
    else:
        huecos = np.array([(f2["dt"] - f1["dt"]).total_seconds()
                           for f1, f2 in zip(fotos, fotos[1:])], dtype=np.float64)
        log_h = np.log1p(np.maximum(huecos, 0))
        # Media móvil local: el "ritmo" de disparo alrededor de cada hueco
        pad = ventana // 2
        extendido = np.pad(log_h, pad, mode="edge")
        kernel = np.ones(ventana) / ventana
        local = np.convolve(extendido, kernel, mode="valid")[:len(log_h)]
        desvio = float(log_h.std()) or 1.0
        cortes = list((log_h > local + k * desvio)
                      | (huecos > horas * 3600)          # guarda dura superior
                      & np.ones(len(huecos), dtype=bool))
        cortes = [bool(c) and h > 1200 for c, h in zip(cortes, huecos)]  # 20 min

    bloques, actual = [], [fotos[0]]
    for corta, foto in zip(cortes, fotos[1:]):
        if corta:
            bloques.append(actual)
            actual = [foto]
        else:
            actual.append(foto)
    bloques.append(actual)
    return bloques


def dividir_bloque(bloque: List[Dict], min_cos: float, max_km: float,
                   min_fotos: int) -> List[List[Dict]]:
    """
    Parte un bloque temporal en eventos por contenido y ubicación.

    Recorre en orden cronológico comparando contra el centroide del evento en
    curso: es lineal, y respeta que las fotos de un evento se parecen entre sí.
    """
    if len(bloque) < min_fotos:
        return [bloque] if bloque else []

    eventos, actual = [], [bloque[0]]
    centroide = bloque[0]["emb"].copy()

    for foto in bloque[1:]:
        cos = float(np.dot(foto["emb"], centroide))

        km = float("inf")
        ultima = actual[-1]
        if foto["lat"] is not None and ultima["lat"] is not None:
            km = distancia_km(foto["lat"], foto["lon"], ultima["lat"], ultima["lon"])

        # Misma cámara en la misma tanda: se afloja la exigencia de contenido,
        # porque una sesión seguida con la misma cámara casi siempre es un evento.
        umbral = min_cos
        if foto["camara"] and foto["camara"] == ultima["camara"]:
            umbral = min_cos * 0.9

        lejos = km != float("inf") and km > max_km
        distinto = cos < umbral

        if lejos or distinto:
            if len(actual) >= min_fotos:
                eventos.append(actual)
            elif eventos:
                eventos[-1].extend(actual)   # grupitos sueltos al evento previo
            else:
                eventos.append(actual)
            actual = [foto]
            centroide = foto["emb"].copy()
        else:
            actual.append(foto)
            # Centroide incremental, renormalizado
            centroide = centroide + (foto["emb"] - centroide) / len(actual)
            n = float(np.linalg.norm(centroide))
            if n > 1e-8:
                centroide /= n

    if actual:
        if len(actual) >= min_fotos or not eventos:
            eventos.append(actual)
        else:
            eventos[-1].extend(actual)
    return eventos


def refundir_no_adyacentes(eventos: List[List[Dict]], horas: float,
                           min_cos: float) -> List[List[Dict]]:
    """
    Segunda pasada: une eventos NO consecutivos que son el mismo momento.

    El recorrido secuencial parte el evento cuando el contenido deriva. Si en un
    cumpleaños se fotografía la torta, después la gente y de nuevo la torta,
    quedan tres eventos. Esto recupera lo que hacía DBSCAN —agrupar por densidad
    sin importar el orden— pero solo dentro de la misma ventana temporal, así
    que sigue siendo barato.
    """
    if len(eventos) < 3:
        return eventos

    centroides = []
    for ev in eventos:
        c = np.mean([f["emb"] for f in ev], axis=0)
        n = float(np.linalg.norm(c))
        centroides.append(c / n if n > 1e-8 else c)

    padre = list(range(len(eventos)))

    def raiz(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    # Solo se comparan eventos dentro de la misma ventana temporal: como están
    # ordenados, alcanza con mirar hacia adelante hasta que el tiempo se pasa.
    for i in range(len(eventos)):
        fin_i = eventos[i][-1]["dt"]
        for j in range(i + 1, len(eventos)):
            gap = (eventos[j][0]["dt"] - fin_i).total_seconds() / 3600.0
            if gap > horas:
                break
            # Umbral más exigente que el de la primera pasada: reunir algo que
            # ya se había separado necesita evidencia más fuerte.
            if float(np.dot(centroides[i], centroides[j])) >= min_cos + 0.08:
                ri, rj = raiz(i), raiz(j)
                if ri != rj:
                    padre[rj] = ri

    grupos: Dict[int, List[Dict]] = {}
    for i, ev in enumerate(eventos):
        grupos.setdefault(raiz(i), []).extend(ev)
    salida = [sorted(g, key=lambda f: f["dt"]) for g in grupos.values()]
    salida.sort(key=lambda g: g[0]["dt"])
    return salida


def fusionar_por_personas(eventos: List[List[Dict]], personas: Dict[int, set],
                          min_comunes: int = 2, horas_max: float = 24.0
                          ) -> List[List[Dict]]:
    """Une eventos consecutivos que comparten al menos N personas."""
    if not personas or len(eventos) < 2:
        return eventos

    def gente(ev):
        s = set()
        for f in ev:
            s |= personas.get(f["id"], set())
        return s

    salida = [eventos[0]]
    for ev in eventos[1:]:
        previo = salida[-1]
        hueco = (ev[0]["dt"] - previo[-1]["dt"]).total_seconds() / 3600.0
        if hueco <= horas_max and len(gente(previo) & gente(ev)) >= min_comunes:
            salida[-1] = previo + ev
        else:
            salida.append(ev)
    return salida


def nombrar(evento: List[Dict]) -> Tuple[str, str, str]:
    """(nombre sugerido, inicio ISO, fin ISO) a partir de escenas, lugar y tags."""
    escenas = Counter(s for f in evento for s in f["escenas"])
    lugares = Counter(f["lugar"] for f in evento if f["lugar"])
    tags = Counter(t for f in evento for t in f["tags"])

    partes = []
    if lugares:
        partes.append(lugares.most_common(1)[0][0])
    if escenas:
        partes.append(escenas.most_common(1)[0][0])
    elif tags:
        partes.append(tags.most_common(1)[0][0])

    inicio = min(f["dt"] for f in evento).isoformat()
    fin = max(f["dt"] for f in evento).isoformat()
    return (" - ".join(partes) if partes else "Evento"), inicio, fin


def agrupar_en_viajes(eventos_info: List[Dict], horas_gap: float,
                      km_max: float, min_eventos: int = 2) -> Dict[int, str]:
    """
    Junta eventos cercanos en tiempo y espacio bajo un viaje.

    Devuelve {event_id: nombre_del_viaje}. Un viaje necesita al menos
    `min_eventos` y abarcar más de un día, si no es solo un evento largo.
    """
    if len(eventos_info) < min_eventos:
        return {}

    grupos, actual = [], [eventos_info[0]]
    for prev, ev in zip(eventos_info, eventos_info[1:]):
        hueco = (ev["inicio"] - prev["fin"]).total_seconds() / 3600.0
        km = distancia_km(ev["lat"], ev["lon"], prev["lat"], prev["lon"])
        # Sin GPS en alguno de los dos NO se asume cercanía: encadenar por tiempo
        # solo produce viajes falsos (p. ej. fotos nocturnas de días seguidos).
        cerca = km <= km_max
        if hueco <= horas_gap and cerca:
            actual.append(ev)
        else:
            grupos.append(actual)
            actual = [ev]
    grupos.append(actual)

    viajes = {}
    for g in grupos:
        if len(g) < min_eventos:
            continue
        inicio = min(e["inicio"] for e in g)
        fin = max(e["fin"] for e in g)
        dias = (fin - inicio).days
        if dias < 1:
            continue     # un solo día no es un viaje
        lugares = Counter(e["lugar"] for e in g if e["lugar"])
        if not lugares:
            continue     # un viaje sin lugar conocido no aporta nada
        # La fecha completa hace único el nombre: dos viajes del mismo mes con
        # igual duración compartirían carpeta si solo se usara el año-mes.
        nombre = (f"{inicio.strftime('%Y-%m-%d')} {lugares.most_common(1)[0][0]} "
                  f"({dias+1} dias)")
        for e in g:
            viajes[e["id"]] = nombre
    return viajes


def _asegurar_columnas(conn: sqlite3.Connection):
    existentes = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    if "viaje" not in existentes:
        conn.execute("ALTER TABLE events ADD COLUMN viaje TEXT")
    conn.commit()


def run(config_path: str = "config.json", force: bool = False):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    horas = float(cfg.get("cluster_time_window_hours", 4))
    min_cos = float(cfg.get("cluster_cosine_threshold", 0.75))
    min_fotos = int(cfg.get("dbscan_min_samples", 4))
    max_km = float(cfg.get("cluster_max_km", 25))
    viaje_gap = float(cfg.get("viaje_gap_horas", 20))
    viaje_km = float(cfg.get("viaje_km", 150))

    conn = get_conn(cfg["db_path"])
    init_db(conn)
    _asegurar_columnas(conn)
    conn.execute("PRAGMA cache_size=-524288")
    conn.execute("PRAGMA temp_store=MEMORY")

    log_step("=" * 60)
    log_step("FASE 3: EVENTOS Y VIAJES")
    log_step(f"  ventana temporal : {horas} h      umbral coseno : {min_cos}")
    log_step(f"  radio mismo lugar: {max_km} km    minimo por evento: {min_fotos}")
    log_step("=" * 60)

    verificados = {r[0] for r in conn.execute("SELECT id FROM events WHERE name IS NOT NULL")}
    if verificados:
        log_step(f"📌 {len(verificados)} eventos con nombre propio: se conservan")
    conn.execute("DELETE FROM event_photos WHERE event_id IN "
                 "(SELECT id FROM events WHERE name IS NULL)")
    conn.execute("DELETE FROM events WHERE name IS NULL")
    conn.commit()

    sp = Spinner("Cargando fotos analizadas...")
    sp.start()
    fotos = cargar_fotos(conn)
    personas = personas_por_foto(conn)
    sp.stop(f"{len(fotos):,} fotos con fecha y embedding"
            + (f" | {len(personas):,} con personas" if personas else ""))

    if len(fotos) < min_fotos:
        log_step("⚠️ Datos insuficientes para agrupar")
        conn.close()
        return

    sp = Spinner("Partiendo por huecos temporales...")
    sp.start()
    bloques = partir_por_tiempo(fotos, horas)
    sp.stop(f"{len(bloques):,} bloques temporales")

    sp = Spinner("Separando eventos por contenido y lugar...")
    sp.start()
    eventos = []
    for i, bloque in enumerate(bloques):
        eventos.extend(dividir_bloque(bloque, min_cos, max_km, min_fotos))
        if i % 200 == 0:
            sp.text = f"Separando eventos... bloque {i:,}/{len(bloques):,}"
    sp.stop(f"{len(eventos):,} eventos antes de fusionar")

    antes = len(eventos)
    eventos = refundir_no_adyacentes(eventos, horas, min_cos)
    if antes != len(eventos):
        log_step(f"🔗 Refundidos por contenido (no adyacentes): {antes - len(eventos)}")

    if personas:
        antes = len(eventos)
        eventos = fusionar_por_personas(eventos, personas)
        log_step(f"👥 Fusionados por personas en común: {antes - len(eventos)}")

    sp = Spinner("Guardando eventos...")
    sp.start()
    info = []
    for ev in eventos:
        nombre, inicio, fin = nombrar(ev)
        eid = create_event(conn, nombre, inicio, fin)
        assign_photos_to_event(conn, eid, [f["id"] for f in ev])
        coords = [(f["lat"], f["lon"]) for f in ev if f["lat"] is not None]
        lugares = Counter(f["lugar"] for f in ev if f["lugar"])
        info.append({
            "id": eid,
            "inicio": _parse(inicio), "fin": _parse(fin),
            "lat": coords[0][0] if coords else None,
            "lon": coords[0][1] if coords else None,
            "lugar": lugares.most_common(1)[0][0] if lugares else None,
        })
    sp.stop(f"{len(info):,} eventos creados")

    viajes = agrupar_en_viajes(info, viaje_gap, viaje_km)
    if viajes:
        conn.executemany("UPDATE events SET viaje=? WHERE id=?",
                         [(n, i) for i, n in viajes.items()])
        conn.commit()
        log_step(f"🧳 Viajes detectados: {len(set(viajes.values()))} "
                 f"({len(viajes)} eventos agrupados)")

    tam = [len(e) for e in eventos]
    log_step("=" * 60)
    log_step("✅ FASE 3 COMPLETADA")
    log_step(f"📁 Eventos     : {len(eventos):,}")
    log_step(f"📸 Fotos       : {sum(tam):,}")
    log_step(f"📈 Por evento  : min {min(tam)}  max {max(tam)}  promedio {sum(tam)/len(tam):.1f}")
    if viajes:
        for nombre in sorted(set(viajes.values()))[:8]:
            log_step(f"   🧳 {nombre}")
    log_step("=" * 60)
    conn.close()


if __name__ == "__main__":
    run()

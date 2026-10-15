"""
Chequeos del corte temporal y la refundición de eventos.

    python test_clustering.py
"""
import importlib
import sys
from datetime import datetime, timedelta

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ag = importlib.import_module("3_agrupacion")


def fotos(minutos_entre, n, emb=None, inicio=None):
    """Secuencia de n fotos separadas por `minutos_entre`."""
    base = inicio or datetime(2024, 1, 1, 12, 0)
    e = emb if emb is not None else np.array([1.0, 0.0], dtype=np.float32)
    return [{"id": i, "dt": base + timedelta(minutes=minutos_entre * i),
             "emb": e, "camara": "X", "lat": None, "lon": None,
             "lugar": None, "escenas": [], "tags": []} for i in range(n)]


def test_guarda_inferior():
    """Ráfaga de fotos cada 10 s: no debe partirse aunque el ritmo varíe."""
    f = fotos(minutos_entre=0.17, n=40)
    b = ag.partir_por_tiempo(f, horas=4)
    assert len(b) == 1, f"una ráfaga debe ser 1 bloque, dio {len(b)}"
    print("OK: guarda inferior (ráfaga de 10 s no se parte)")


def test_guarda_superior():
    """Un hueco enorme siempre corta, aunque el ritmo local sea irregular."""
    a = fotos(minutos_entre=5, n=12)
    b = fotos(minutos_entre=5, n=12, inicio=datetime(2024, 1, 3, 12, 0))
    bloques = ag.partir_por_tiempo(a + b, horas=4)
    assert len(bloques) >= 2, f"2 días distintos deben separarse, dio {len(bloques)}"
    assert all(x["dt"].day == 1 for x in bloques[0])
    print("OK: guarda superior (2 días separados)")


def test_adaptativo_detecta_cambio_de_ritmo():
    """Fotos cada 2 min y de golpe un hueco de 90 min: debe cortar ahí."""
    a = fotos(minutos_entre=2, n=15)
    b = fotos(minutos_entre=2, n=15,
              inicio=a[-1]["dt"] + timedelta(minutes=90))
    bloques = ag.partir_por_tiempo(a + b, horas=4)
    assert len(bloques) == 2, f"esperaba 2 bloques, dio {len(bloques)}"
    assert len(bloques[0]) == 15, f"el corte quedó mal: {len(bloques[0])}"
    print("OK: adaptativo corta un hueco de 90 min dentro de la ventana de 4 h")


def test_modo_fijo_sigue_disponible():
    a = fotos(minutos_entre=2, n=15)
    b = fotos(minutos_entre=2, n=15, inicio=a[-1]["dt"] + timedelta(minutes=90))
    # Con umbral fijo de 4 h, 90 min NO corta
    bloques = ag.partir_por_tiempo(a + b, horas=4, adaptativo=False)
    assert len(bloques) == 1, f"modo fijo no debe cortar a 90 min, dio {len(bloques)}"
    print("OK: modo fijo conserva el comportamiento anterior")


def test_refundir_no_adyacentes():
    """torta -> gente -> torta debe volver a ser un evento."""
    torta = np.array([1.0, 0.0], dtype=np.float32)
    gente = np.array([0.0, 1.0], dtype=np.float32)
    base = datetime(2024, 5, 1, 18, 0)
    ev = [
        [{"id": 1, "dt": base, "emb": torta}],
        [{"id": 2, "dt": base + timedelta(minutes=20), "emb": gente}],
        [{"id": 3, "dt": base + timedelta(minutes=40), "emb": torta}],
    ]
    r = ag.refundir_no_adyacentes(ev, horas=4, min_cos=0.75)
    assert len(r) == 2, f"esperaba 2 eventos (torta unida + gente), dio {len(r)}"
    tam = sorted(len(x) for x in r)
    assert tam == [1, 2], f"tamaños {tam}"
    print("OK: refunde momentos no adyacentes del mismo evento")


def test_refundir_respeta_el_tiempo():
    """Contenido igual pero separado por días: NO debe unirse."""
    e = np.array([1.0, 0.0], dtype=np.float32)
    ev = [
        [{"id": 1, "dt": datetime(2024, 5, 1, 12, 0), "emb": e}],
        [{"id": 2, "dt": datetime(2024, 5, 20, 12, 0), "emb": e}],
    ]
    r = ag.refundir_no_adyacentes(ev, horas=4, min_cos=0.75)
    assert len(r) == 2, "no debe unir eventos separados por semanas"
    print("OK: no refunde a través del tiempo")


def test_distancia_km():
    # Santiago - Valparaiso ~ 100 km
    d = ag.distancia_km(-33.4489, -70.6693, -33.0472, -71.6127)
    assert 90 < d < 115, f"Santiago-Valparaiso dio {d:.0f} km"
    assert ag.distancia_km(None, None, -33.0, -71.0) == float("inf")
    print(f"OK: haversine (Santiago-Valparaiso {d:.0f} km)")


if __name__ == "__main__":
    test_distancia_km()
    test_guarda_inferior()
    test_guarda_superior()
    test_adaptativo_detecta_cambio_de_ritmo()
    test_modo_fijo_sigue_disponible()
    test_refundir_no_adyacentes()
    test_refundir_respeta_el_tiempo()
    print("\n✅ TODOS LOS CHEQUEOS DE CLUSTERING PASARON")

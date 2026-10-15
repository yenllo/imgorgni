"""python test_read_gps_timeout.py

read_gps colgaba varios segundos por foto cuando exifread entraba en
recursion cara sobre un IFD GPS corrupto (medido con py-spy en la Fase 2 real:
5+ s/foto en material de OneDrive, contra ~0.15 s/foto normal). Un hilo con
timeout evita bloquear, pero se probo en vivo y quedaba zombie peleando el GIL
-- 9 acumulados en el primer minuto -- asi que la version final mata un
PROCESO de verdad con terminate() y lo reemplaza.

Mockear exifread no sirve para probar esto: con multiprocessing en Windows
(spawn) el proceso hijo re-importa todo desde cero y no hereda los mocks del
proceso de test. Por eso _TrabajadorGPS acepta un worker inyectable: los
tests de aca usan uno falso que se cuelga a proposito, sin tocar exifread.
"""
import multiprocessing as mp
import os
import tempfile
import time

import utils_analisis as ua


def _worker_normal(entrada, salida):
    """Responde rapido con coords fijas, como un exifread sano."""
    while True:
        fp = entrada.get()
        if fp is None:
            return
        salida.put((10.5, -20.5))


def _worker_colgado(entrada, salida):
    """Nunca responde al primer pedido: simula el IFD GPS corrupto."""
    while True:
        fp = entrada.get()
        if fp is None:
            return
        time.sleep(30)
        salida.put((0.0, 0.0))  # no deberia llegar nunca en el test


def test_worker_normal_responde_rapido():
    t = ua._TrabajadorGPS(worker=_worker_normal)
    t0 = time.time()
    lat, lon = t.leer("cualquier_ruta.jpg", timeout=1.5)
    dur = time.time() - t0
    assert (lat, lon) == (10.5, -20.5), (lat, lon)
    assert dur < 1.0, f"un worker sano no puede tardar {dur:.2f}s"
    t.proc.terminate()
    print("OK: worker sano responde rapido")


def test_worker_colgado_se_mata_y_no_bloquea():
    t = ua._TrabajadorGPS(worker=_worker_colgado)
    t0 = time.time()
    lat, lon = t.leer("cualquier_ruta.jpg", timeout=0.4)
    dur = time.time() - t0
    assert (lat, lon) == (None, None)
    assert dur < 1.5, f"tenia que cortar cerca de 0.4s, tardo {dur:.2f}s"
    assert t.proc is None, "despues de matarlo tiene que quedar None (se reemplaza solo)"
    print(f"OK: worker colgado se corta en ~{dur:.2f}s")


def test_terminate_libera_de_verdad_no_deja_zombie():
    """La diferencia clave con el hilo: terminate() mata el proceso YA, no lo
    deja corriendo de fondo peleando por CPU/GIL."""
    t = ua._TrabajadorGPS(worker=_worker_colgado)
    t.leer("x.jpg", timeout=0.3)
    pid_muerto = None
    # el proceso que se colgo ya no puede seguir vivo tras terminate()+join()
    procesos_vivos = [p for p in mp.active_children()]
    assert len(procesos_vivos) == 0, \
        f"quedaron procesos hijos vivos: {procesos_vivos} (fuga como la del hilo)"
    print("OK: terminate() no deja ningun proceso zombie corriendo")


def test_se_recupera_para_el_siguiente_pedido():
    """Despues de matar uno colgado, el SIGUIENTE pedido tiene que andar
    normal (se levanta un worker nuevo solo)."""
    t = ua._TrabajadorGPS(worker=_worker_colgado)
    t.leer("primero.jpg", timeout=0.3)   # este se cuelga y se mata
    t.worker = _worker_normal            # el segundo pedido usa un worker sano
    lat, lon = t.leer("segundo.jpg", timeout=1.5)
    assert (lat, lon) == (10.5, -20.5), \
        "despues de matar el colgado, el siguiente pedido tiene que funcionar"
    t.proc.terminate()
    print("OK: se recupera solo despues de matar uno colgado")


def test_read_gps_archivo_real_sin_exif():
    """Sobre un archivo real sin GPS, read_gps tiene que volver rapido (no
    hay IFD corrupto que la dispare: es el camino normal de verdad)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"no es un jpeg real, solo bytes")
        ruta = f.name
    try:
        t0 = time.time()
        lat, lon = ua.read_gps(ruta, timeout=1.5)
        dur = time.time() - t0
        assert dur < 1.0, f"un archivo sin EXIF valido no puede tardar {dur:.2f}s"
    finally:
        os.remove(ruta)
    print(f"OK: read_gps real sobre archivo sin EXIF valido, {dur:.2f}s")


if __name__ == "__main__":
    test_worker_normal_responde_rapido()
    test_worker_colgado_se_mata_y_no_bloquea()
    test_terminate_libera_de_verdad_no_deja_zombie()
    test_se_recupera_para_el_siguiente_pedido()
    test_read_gps_archivo_real_sin_exif()

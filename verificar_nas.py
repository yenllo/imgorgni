"""Chequea que K: montado por red sirva para el pipeline: rutas de la base + velocidad real."""
import os
import sqlite3
import time

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos_v2.db")
MUESTRA = 50


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    total, = con.execute("select count(*) from files where borrado=0").fetchone()
    rutas = [r[0] for r in con.execute(
        "select file_path from files where borrado=0 order by random() limit ?", (MUESTRA,))]
    con.close()

    def existe(p, intentos=3):
        # ponytail: os.path.exists() traga cualquier error de red y devuelve
        # False; sobre SMB a 35MB/s (USB2, ver hallazgos) un stat() puede
        # tardar/fallar de forma transitoria. Reintentar antes de dar por
        # faltante evita falsos positivos (confirmado: archivo "faltante"
        # visible acto seguido con dir). Subir intentos si sigue dando ruido.
        for i in range(intentos):
            if os.path.exists(p):
                return True
            time.sleep(0.3)
        return False

    faltan = [p for p in rutas if not existe(p)]
    assert not faltan, (
        f"{len(faltan)}/{len(rutas)} rutas de la base no existen. Ejemplos: {faltan[:3]}\n"
        "La letra K: no apunta al share, o el share no tiene el arbol completo.")

    # Lee 1 MB de cada archivo: sobre SMB esto mide latencia por archivo, que es
    # lo que realmente duele con 225k archivos, no el ancho de banda secuencial.
    t0 = time.perf_counter()
    leidos = 0
    for p in rutas:
        with open(p, "rb") as f:
            leidos += len(f.read(1 << 20))
    dt = time.perf_counter() - t0

    print(f"OK  {len(rutas)}/{len(rutas)} rutas existen (de {total:,} vivas en la base)")
    mbs = leidos / dt / 1e6
    print(f"    {mbs:.0f} MB/s  |  {dt / len(rutas) * 1000:.0f} ms por archivo")
    # Baseline medido 2026-08-04 con K: por USB3 directo: 24 MB/s, 37 ms/archivo.
    # El cuello es el seek del HDD, no el bus: por LAN gigabit deberia dar parecido.
    if mbs < 12:
        print("    OJO: menos de la mitad del baseline USB (24 MB/s). Estas por Tailscale, o algo anda mal.")


if __name__ == "__main__":
    main()

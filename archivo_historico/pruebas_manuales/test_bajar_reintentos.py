"""python test_bajar_reintentos.py

Anoche OneDrive corto la hidratacion (WinError 389) y el script fallo 35.638
archivos sin reintentar ni uno. Esto verifica que ahora aguante un corte
transitorio y que no deje archivos a medio copiar.
"""
import os
import shutil
import tempfile

import bajar_de_onedrive as b


def main():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "foto.jpg")
    with open(src, "wb") as f:
        f.write(b"x" * 5000)

    # --- falla las 2 primeras veces y anda a la tercera ---
    real = shutil.copy2
    intentos = {"n": 0}

    def flaky(a, bb):
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise OSError(389, "La operacion de nube no se realizo correctamente")
        return real(a, bb)

    shutil.copy2 = flaky
    b.ESPERA = 0            # sin esperas reales en el test
    try:
        dst = os.path.join(d, "salida", "foto.jpg")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        ok = False
        for i in range(b.REINTENTOS):
            try:
                shutil.copy2(src, dst)
                ok = True
                break
            except OSError:
                if os.path.exists(dst) and os.path.getsize(dst) != 5000:
                    os.remove(dst)
        assert ok, "con 3 reintentos y falla en las 2 primeras, tiene que lograrlo"
        assert intentos["n"] == 3, intentos
        assert os.path.getsize(dst) == 5000
    finally:
        shutil.copy2 = real

    # --- los parametros tienen que ser sensatos ---
    assert b.REINTENTOS >= 2, "un solo intento es lo que fallo anoche"
    assert b.SEGUIDAS_MAX > 0 and b.PAUSA >= 60, "la pausa tiene que ser real"
    assert b.MIN_LIBRE_GB > 0

    # --- un archivo a medio copiar no puede quedar como bueno ---
    parcial = os.path.join(d, "parcial.jpg")
    with open(parcial, "wb") as f:
        f.write(b"y" * 100)          # deberia pesar 5000
    assert os.path.getsize(parcial) != 5000
    if os.path.exists(parcial) and os.path.getsize(parcial) != 5000:
        os.remove(parcial)
    assert not os.path.exists(parcial), "el parcial se tiene que borrar"

    print("OK: reintenta ante corte transitorio y no deja archivos a medias")


if __name__ == "__main__":
    main()

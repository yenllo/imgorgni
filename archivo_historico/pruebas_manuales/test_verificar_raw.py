"""python test_verificar_raw.py

Lo que importa: un archivo SANO no puede dar 'roto'. Un falso positivo manda a
buscar en OneDrive algo que ya estaba bien.
"""
import os
import tempfile

from PIL import Image

from verificar_raw import NO_SOPORTADO, OK, ROTO, decodificador, revisar


def main():
    d = tempfile.mkdtemp()

    # El ruteo: los .tif no pueden ir a rawpy (daria 'roto' a archivos sanos)
    assert decodificador("x.CR2") == "rawpy"
    assert decodificador("x.arw") == "rawpy"
    assert decodificador("x.dng") == "rawpy"
    assert decodificador("x.tif") == "pil", "un TIFF no es RAW de sensor"
    assert decodificador("x.mpo") == "pil"
    assert decodificador("x.jpg") is None, "el JPEG ya lo cubre el otro verificador"

    # TIFF sano -> decodifica
    sano = os.path.join(d, "sano.tif")
    Image.new("RGB", (64, 48), (10, 120, 200)).save(sano)
    estado, detalle = revisar(sano)
    assert estado == OK, f"un TIFF sano dio {estado!r} ({detalle})"

    # el mismo TIFF cortado a la mitad -> roto
    roto = os.path.join(d, "roto.tif")
    datos = open(sano, "rb").read()
    with open(roto, "wb") as f:
        f.write(datos[:len(datos) // 2])
    estado, _ = revisar(roto)
    assert estado == ROTO, "un TIFF truncado tiene que dar roto"

    # archivo vacio -> roto, no explota
    vacio = os.path.join(d, "vacio.dng")
    open(vacio, "wb").close()
    assert revisar(vacio)[0] == ROTO

    # extension que no corresponde verificar acá
    assert revisar(os.path.join(d, "foto.jpg"))[0] is None

    # "no soporto el formato" NO es daño. Un DNG lineal de HDR/panoramica de
    # Lightroom esta sano y LibRaw igual no lo abre: si eso contara como roto,
    # mandaria a rebuscar en OneDrive archivos perfectos.
    falso = os.path.join(d, "hdr.tif")
    with open(falso, "wb") as f:
        f.write(b"no soy una imagen")        # PIL: UnidentifiedImageError
    estado, detalle = revisar(falso)
    assert estado == NO_SOPORTADO, f"dio {estado!r} ({detalle}), esperaba no-soportado"
    assert estado != ROTO

    print("OK: rutea por formato, detecta truncado, y no confunde "
          "'no soportado' con 'dañado'")


if __name__ == "__main__":
    main()

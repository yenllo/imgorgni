"""python test_editado.py

El pill "editado" empuja a descartar esa copia. Marcarlo de mas es peor que de
menos: hace tirar originales intactos.
"""
from web_curador import es_editado, info_archivo


def main():
    # camaras: no son ediciones
    assert not es_editado("ILCE-7RM3 v3.10")
    assert not es_editado("ILCE-7 v3.20")
    assert not es_editado(None)
    assert not es_editado("")

    # editores de verdad
    assert es_editado("Adobe Photoshop Lightroom Classic 15.0 (Windows)")
    assert es_editado("Adobe Photoshop CC 2019")
    assert es_editado("GIMP 2.10")
    assert es_editado("Snapseed")

    # el caso que motivo el arreglo: convertir a DNG no es editar
    assert not es_editado("Adobe DNG Converter 18.0 (Windows)"), \
        "convertir a DNG cambia el contenedor, no la imagen"
    assert not es_editado("adobe dng converter"), "tiene que dar igual la caja"

    # y sigue detectando Lightroom aunque tambien diga Adobe
    assert es_editado("Adobe Photoshop Lightroom Classic 13.3.1 (Windows)")

    # info_archivo arma el formato desde la extension, sin tocar el disco
    i = info_archivo(r"K:\x\foto.CR2", "ILCE-7 v3.20")
    assert i["formato"] == "RAW" and i["editado"] is False
    i = info_archivo(r"K:\x\foto.jpg", "Adobe Photoshop Lightroom Classic 15.0")
    assert i["formato"] == "JPG" and i["editado"] is True
    assert info_archivo(None)["formato"] is None

    print("OK: no confunde convertir a DNG con editar, ni la camara con un editor")


if __name__ == "__main__":
    main()

"""python test_plan_rescate_local.py

DSC01234.JPG se repite entre tarjetas de memoria: emparejar solo por nombre
junta fotos distintas. Por eso la confianza baja cuando fecha o tamaño no dan.
"""
from plan_rescate_local import DEBIL, FUERTE, MEDIA, elegir


def c(ruta, fecha, bytes_):
    return {"ruta": ruta, "fecha": fecha, "bytes": bytes_}


def main():
    # coincide todo -> confianza fuerte
    cand, conf = elegir([c("A", "2025-10-18", 5000)], "2025-10-18", 5000)
    assert (cand["ruta"], conf) == ("A", FUERTE)

    # misma fecha, otro tamaño (reexportada) -> media
    cand, conf = elegir([c("B", "2025-10-18", 9999)], "2025-10-18", 5000)
    assert (cand["ruta"], conf) == ("B", MEDIA)

    # otra fecha -> es otra foto que se llama igual: debil, hay que mirarla
    cand, conf = elegir([c("C", "2001-01-01", 5000)], "2025-10-18", 5000)
    assert (cand["ruta"], conf) == ("C", DEBIL)

    # con varios candidatos gana el que coincide en fecha Y tamaño
    cands = [c("mal", "2001-01-01", 1), c("bien", "2025-10-18", 5000),
             c("regular", "2025-10-18", 77)]
    cand, conf = elegir(cands, "2025-10-18", 5000)
    assert (cand["ruta"], conf) == ("bien", FUERTE), cand

    # sin fecha conocida no se puede afirmar nada fuerte
    cand, conf = elegir([c("D", "", 5000)], "", 5000)
    assert conf == DEBIL, "sin fecha no hay confirmacion posible"

    # sin candidatos
    assert elegir([], "2025-10-18", 5000) == (None, None)

    print("OK: no promete confianza que no tiene, y elige el mejor candidato")


if __name__ == "__main__":
    main()

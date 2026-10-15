"""
Chequeos del OCR para nombrar eventos.

Lo delicado no es leer el texto sino DECIDIR cuál sirve como nombre de carpeta:
un pase de embarque o un "Sin titulo" no son eventos.

    python test_ocr.py
"""
import sys

from utils_ocr import pista_de_evento, texto_util, _sin_tildes

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def test_acepta_carteles_reales():
    casos = [
        ("Feliz Cumpleanos Marta", "cartel de cumpleaños"),
        ("Feliz Cumpleaños Marta", "con tilde"),
        ("FINAL COPA 2024", "evento deportivo"),
        ("Bienvenidos a la graduacion", "plural + sin tilde"),
        ("Graduación 2025", "con tilde"),
        ("Torneo de verano", "torneo"),
        ("Feliz Navidad", "navidad"),
    ]
    for texto, desc in casos:
        got = pista_de_evento([(texto, 0.9)])
        assert got is not None, f"deberia aceptar [{desc}]: '{texto}'"
    print(f"OK: acepta {len(casos)} carteles reales")


def test_rechaza_ruido():
    casos = [
        ("Elembarquefinalizaraalas5:02", "OCR mal segmentado ('final' adentro)"),
        ("Sin titulo", "generico"),
        ("Screenshot", "generico"),
        ("untitled", "generico en ingles"),
        ("12345 67890 999 000", "solo numeros"),
        ("El vuelo LA841 finalizara su embarque en la puerta 22 del aeropuerto",
         "parrafo largo"),
    ]
    for texto, desc in casos:
        got = pista_de_evento([(texto, 0.95)])
        assert got is None, f"deberia rechazar [{desc}]: '{texto}' -> dio '{got}'"
    print(f"OK: rechaza {len(casos)} casos de ruido")


def test_limite_de_palabra():
    # "final" no debe matchear adentro de otra palabra
    assert pista_de_evento([("finalizando el tramite", 0.9)]) is None
    # pero sí como palabra suelta
    assert pista_de_evento([("Final del torneo", 0.9)]) is not None
    print("OK: limite de palabra ('finalizando' no, 'Final' si)")


def test_prefiere_el_mejor():
    bloques = [("texto irrelevante largo sin nada", 0.99),
               ("Cumpleanos de Simon", 0.85),
               ("otra cosa", 0.9)]
    assert pista_de_evento(bloques) == "Cumpleanos de Simon"
    print("OK: elige el cartel entre varios textos")


def test_texto_util():
    bloques = [("corto", 0.9), ("un texto bastante mas largo y confiable", 0.95)]
    r = texto_util(bloques)
    assert "largo" in r and len(r) <= 120
    assert texto_util([]) == ""
    print("OK: texto_util prioriza y limita")


def test_sin_tildes():
    assert _sin_tildes("Graduación") == "graduacion"
    assert _sin_tildes("CUMPLEAÑOS") == "cumpleanos"
    print("OK: normalizacion de tildes")


if __name__ == "__main__":
    test_sin_tildes()
    test_limite_de_palabra()
    test_acepta_carteles_reales()
    test_rechaza_ruido()
    test_prefiere_el_mejor()
    test_texto_util()
    print("\n✅ TODOS LOS CHEQUEOS DE OCR PASARON")

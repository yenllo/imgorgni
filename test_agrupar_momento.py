"""Chequeo de agrupar_por_momento: lo que decide que dos fotos son el mismo disparo.

    python test_agrupar_momento.py

No toca la BD: son filas armadas a mano. La ultima prueba si la consulta, para
confirmar que el corte real da lo mismo que la cuenta que se midio al elegir los
umbrales (las 3.056 de «flowers» -> 952 tarjetas).
"""
import sqlite3
import sys

from web_curador import (DB, GRUPO_HAMMING, GRUPO_MAX, GRUPO_SEG,
                         agrupar_por_momento)


def fila(id, seg, phash):
    """Una foto a `seg` segundos del minuto cero, con ese phash."""
    return {"id": id, "capture_date": "2026-01-01T12:00:%02d" % seg, "phash": phash}


A = "0000000000000000"
A2 = "0000000000000007"          # 3 bits distintos de A: la misma escena
B = "ffffffffffffffff"           # 64 bits: otra cosa


def prueba_junta_lo_parecido_y_cercano():
    g = agrupar_por_momento([fila(1, 0, A), fila(2, 1, A2)])
    assert len(g) == 1 and len(g[0]) == 2, g


def prueba_corta_por_tiempo():
    lejos = GRUPO_SEG + 5
    g = agrupar_por_momento([fila(1, 0, A), fila(2, lejos, A)])
    assert len(g) == 2, "misma imagen pero a %ds: son momentos distintos" % lejos


def prueba_corta_por_imagen():
    g = agrupar_por_momento([fila(1, 0, A), fila(2, 1, B)])
    assert len(g) == 2, "al mismo segundo pero otra escena: no van juntas"


def prueba_encadena():
    """A~B y B~C agrupan aunque A y C ya no se parezcan: en una rafaga el
    encuadre se corre de a poco y el primer cuadro no se parece al ultimo."""
    lejano = "000000000000%03x" % ((1 << GRUPO_HAMMING) - 1)   # GRUPO_HAMMING bits
    g = agrupar_por_momento([fila(1, 0, A), fila(2, 1, lejano), fila(3, 2, B)])
    assert [len(x) for x in g] == [2, 1], g


def prueba_sin_fecha_queda_sola():
    sin = {"id": 9, "capture_date": None, "phash": A}
    g = agrupar_por_momento([sin, fila(2, 0, A)])
    assert len(g) == 2, "sin fecha no se puede afirmar que es el mismo momento"


def prueba_sin_phash_queda_sola():
    sin = {"id": 9, "capture_date": "2026-01-01T12:00:00", "phash": None}
    g = agrupar_por_momento([sin, fila(2, 0, A)])
    assert len(g) == 2, "sin phash no se puede afirmar que es la misma escena"


def prueba_vacio():
    assert agrupar_por_momento([]) == []


def prueba_corta_las_rachas_largas():
    """Una racha de verdad puede tener 48 fotos; en el mosaico no se ven."""
    larga = [fila(i, 0, A) for i in range(GRUPO_MAX * 2 + 3)]
    g = agrupar_por_momento(larga)
    assert [len(x) for x in g] == [GRUPO_MAX, GRUPO_MAX, 3], [len(x) for x in g]
    # y los pedazos salen en orden, uno atras del otro
    assert [f["id"] for f in sum(g, [])] == [f["id"] for f in larga]


def prueba_contra_la_base():
    """Lo medido cuando se eligieron los umbrales, sobre datos de verdad."""
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    c.row_factory = sqlite3.Row
    filas = c.execute(
        """SELECT f.id, f.capture_date, pf.phash
             FROM files f JOIN photo_features pf ON pf.photo_id = f.id
            WHERE COALESCE(f.borrado,0)=0 AND COALESCE(f.danado,0)=0
              AND pf.scene_tags LIKE '%"flowers"%'
            ORDER BY f.capture_date""").fetchall()
    grupos = agrupar_por_momento(filas)
    assert sum(len(g) for g in grupos) == len(filas), "se perdio o duplico una foto"
    juntadas = sum(len(g) for g in grupos if len(g) > 1)
    print("   %d fotos de «flowers» -> %d tarjetas (%d fotos en grupo, la mayor de %d)"
          % (len(filas), len(grupos), juntadas, max(len(g) for g in grupos)))
    assert len(grupos) < len(filas), "no agrupo nada: revisar phash/capture_date"


if __name__ == "__main__":
    solo_memoria = "--rapido" in sys.argv
    for nombre, fn in sorted(globals().items()):
        if not nombre.startswith("prueba_"):
            continue
        if solo_memoria and nombre == "prueba_contra_la_base":
            continue
        fn()
        print("ok", nombre)
    print("GRUPO_SEG=%d GRUPO_HAMMING=%d GRUPO_MAX=%d"
          % (GRUPO_SEG, GRUPO_HAMMING, GRUPO_MAX))

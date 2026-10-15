r"""
Verificador de "copia mejor": para cada duplicado ya resuelto (descartar o
movido a cuarentena), confirma que el archivo que se conservo sigue vivo -
existe en disco, no esta en el basurero, no esta el el mismo en cuarentena,
y no es a su vez el lado descartado de otro par. Sin este chequeo, "aplicar"
podria dejar una foto sin ninguna copia en pie.

    python verificar_copia_mejor.py
"""
import os
import sqlite3

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
CUARENTENA = r"k:\_duplicados_cuarentena"


def verificar(c, existe=os.path.exists):
    """Lista de problemas entre los duplicados ya resueltos (descartar/movido)."""
    filas = c.execute("""
        SELECT d.id, d.decision, d.id_conservar, d.id_descartar,
               fc.file_path AS ruta_conservar, fc.borrado AS conservar_borrado
        FROM duplicados d
        LEFT JOIN files fc ON fc.id = d.id_conservar
        WHERE d.decision IN ('descartar', 'movido')
    """).fetchall()

    # si el mismo id aparece como "conservar" en un par y "descartar" en otro
    # (ambos ya resueltos), la copia que un par da por buena el otro la borra.
    en_cadena = ({r["id_conservar"] for r in filas} &
                {r["id_descartar"] for r in filas})

    # Direccion inversa: el archivo que este par quiere borrar puede ser la copia
    # buena de OTRO par que todavia no se resolvio. Borrarlo deja a ese otro par
    # sin nada que conservar. El chequeo de arriba no lo ve porque solo mira
    # pares ya resueltos; en agosto 2026 este caso aparecio en 3.292 pares y se
    # detecto a mano, justo antes de borrar 392 GB.
    depende_de = {r[0] for r in c.execute("""
        SELECT DISTINCT d.id_conservar FROM duplicados d
        JOIN files f ON f.id = d.id_descartar
        WHERE d.decision NOT IN ('omitido', 'conservar')
    """)}

    problemas = []
    for f in filas:
        motivo = None
        if f["ruta_conservar"] is None:
            motivo = "id_conservar no existe en files (huerfano)"
        elif f["conservar_borrado"]:
            motivo = "el conservar esta en el basurero"
        elif f["id_conservar"] in en_cadena:
            motivo = "el conservar es a su vez el descartado de otro par"
        elif (f["ruta_conservar"] or "").lower().startswith(CUARENTENA):
            motivo = "el conservar ya esta en cuarentena"
        elif not existe(f["ruta_conservar"]):
            motivo = "el conservar no esta en el disco"
        elif f["id_descartar"] in depende_de:
            motivo = "el descartar es la copia buena de otro par sin resolver"
        if motivo:
            problemas.append({"id": f["id"], "decision": f["decision"],
                              "id_descartar": f["id_descartar"],
                              "ruta_conservar": f["ruta_conservar"], "motivo": motivo})
    return problemas


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    total = c.execute(
        "SELECT COUNT(*) n FROM duplicados WHERE decision IN ('descartar','movido')"
    ).fetchone()["n"]
    print(f"chequeando {total:,} duplicados ya resueltos (descartar + movido)...")
    problemas = verificar(c)
    if not problemas:
        print("OK: todos los descartados tienen una copia conservada viva")
        return
    print(f"\n{len(problemas)} problema(s):\n")
    for p in problemas[:300]:
        print(f"  [{p['decision']}] duplicados.id={p['id']} (id_descartar={p['id_descartar']}) "
              f"-- {p['motivo']}")
        print(f"      conservar: {p['ruta_conservar']}")
    if len(problemas) > 300:
        print(f"  ... y {len(problemas) - 300} mas")


if __name__ == "__main__":
    main()

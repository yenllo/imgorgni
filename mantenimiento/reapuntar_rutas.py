"""Reapunta files.file_path a donde los archivos estan REALMENTE hoy.

La DB quedo desincronizada: el 89% de las rutas apuntan a ubicaciones que ya no
existen porque los archivos se movieron a K:\\galeria sin actualizar la base.
Esto los vuelve a encontrar y reescribe SOLO la columna file_path. No mueve, no
borra, no toca eventos, caras, tags ni veredictos.

    python reapuntar_rutas.py             # simulacion, no escribe nada
    python reapuntar_rutas.py --aplicar   # escribe
    python reapuntar_rutas.py --test      # autochequeo de la logica de match

Estrategia, de barata a cara:
  1. La ruta actual existe -> no se toca.
  2. Un unico archivo en disco con el mismo (nombre, bytes) -> se reapunta.
     Es metadata pura, no lee contenido. Resuelve la enorme mayoria.
  3. Varios candidatos -> se hashea SOLO esos y se desempata por sha256.
  4. Ninguno -> se reporta como no encontrado; no se inventa nada.

ponytail: el match por (nombre, bytes) evita hashear 3,5 TB. Si algun dia dos
fotos distintas comparten nombre Y tamano exacto, el paso 3 las desempata.
"""

import argparse
import collections
import hashlib
import os
import sqlite3
import sys
import time

DB = "photos_v2.db"
RAIZ = "K:" + os.sep
EXT = {".jpg", ".jpeg", ".arw", ".dng", ".png", ".cr2", ".nef",
       ".tif", ".tiff", ".heic", ".webp", ".gif", ".bmp"}
SALTAR = ("$recycle", "system volume information")

# Carpetas DERIVADAS: copias que produjo el pipeline, no originales. Un archivo
# de aca solo se elige si no queda ninguna otra copia. Sin esto la DB termina
# apuntando a un export en vez de al original, que fue lo que paso la 1a vez.
DERIVADOS = (os.sep + "fotos_organizadas_ia_v3" + os.sep,
             os.sep + "_previews" + os.sep)


def es_derivado(p):
    return any(s in (p or "").lower() for s in DERIVADOS)


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()


def indexar_disco(raiz=RAIZ):
    """{(nombre_minuscula, bytes): [rutas]} de todas las imagenes bajo raiz."""
    idx = collections.defaultdict(list)
    n = 0
    for root, dirs, files in os.walk(raiz):
        low = root.lower()
        if any(s in low for s in SALTAR):
            dirs[:] = []
            continue
        for f in files:
            if os.path.splitext(f)[1].lower() not in EXT:
                continue
            p = os.path.join(root, f)
            try:
                idx[(f.lower(), os.path.getsize(p))].append(p)
            except OSError:
                continue
            n += 1
    return idx, n


def _avance(hechos, total, t0, st):
    """Una linea de progreso con ETA. El costo real es hashear ambiguos."""
    seg = time.time() - t0
    rest = (total - hechos) * seg / hechos if hechos else 0
    print(f"  {hechos:>7,}/{total:,}  {100*hechos/total:5.1f}%  "
          f"transcurrido {seg/60:5.1f} min  faltan ~{rest/60:5.1f} min  "
          f"(hasheados {st['por_hash']:,})", flush=True)


def resolver(filas, idx, progreso=False):
    """Decide una ruta nueva por fila. Devuelve (cambios, stats).

    filas: [(id, file_path, bytes, sha)]. Un archivo de disco no puede ser
    reclamado por dos filas distintas: la primera que lo toma se lo queda.
    """
    cambios = []
    st = collections.Counter()
    tomados = set()
    t0 = time.time()

    # PASADA A: reservar toda ruta que hoy existe y ya tiene dueno. Sin esto,
    # una fila temprana puede robarle el archivo a una posterior que lo ocupa
    # legitimamente, y las dos terminan apuntando al mismo path (viola UNIQUE).
    # Una fila que apunta a un derivado se reevalua aunque el archivo exista:
    # queremos el original si todavia esta en algun lado.
    pendientes = []
    for fid, ruta, tam, sha in filas:
        if ruta and os.path.exists(ruta) and not es_derivado(ruta):
            st["ya_ok"] += 1
            tomados.add(ruta.lower())
        else:
            pendientes.append((fid, ruta, tam, sha))

    # PASADA B: resolver el resto. Aca esta el costo (hashear ambiguos).
    total = len(pendientes)
    for i, (fid, ruta, tam, sha) in enumerate(pendientes, 1):
        if progreso and i % 5000 == 0:
            _avance(i, total, t0, st)

        cands = [p for p in idx.get((os.path.basename(ruta or "").lower(), tam), [])
                 if p.lower() not in tomados]
        if not cands:
            st["no_encontrado"] += 1
            continue
        # originales primero; un derivado solo si no hay otra cosa
        cands.sort(key=es_derivado)

        if len(cands) == 1:
            elegido = cands[0]
            st["por_nombre_tamano"] += 1
        else:
            elegido = None
            if sha:
                for p in cands:
                    try:
                        if sha256(p) == sha:
                            elegido = p
                            break
                    except OSError:
                        continue
            if elegido is None:
                st["ambiguo"] += 1
                continue
            st["por_hash"] += 1

        if es_derivado(elegido):
            st["solo_derivado"] += 1
        tomados.add(elegido.lower())
        if elegido != ruta:
            cambios.append((fid, ruta, elegido))
        else:
            st["ya_ok"] += 1
    return cambios, st


def escribir(con, cambios):
    """Aplica los cambios respetando el UNIQUE de files.file_path.

    SQLite valida el UNIQUE en CADA update, no al final: si A debe tomar la
    ruta que B tiene hoy, el update de A choca aunque al terminar nadie
    colisione. Aparcar primero todas las filas afectadas en un valor unico
    temporal rompe esas cadenas y tambien los ciclos. Todo en una transaccion:
    si algo falla no queda ninguna fila aparcada.
    """
    destinos = collections.Counter(n.lower() for _, _, n in cambios)
    dup = [r for r, c in destinos.items() if c > 1]
    if dup:
        raise ValueError(f"{len(dup)} rutas destino repetidas, p.ej. {dup[0]}")

    try:
        # sqlite3 ya abre transaccion implicita en el primer DML y la sostiene
        # hasta commit(): las dos fases van juntas o no va ninguna.
        con.executemany("update files set file_path='@@TMP@@'||id where id=?",
                        [(fid,) for fid, _, _ in cambios])
        con.executemany("update files set file_path=? where id=?",
                        [(nuevo, fid) for fid, _, nuevo in cambios])
        con.commit()
    except Exception:
        con.rollback()
        raise

    quedan = con.execute(
        "select count(*) from files where file_path like '@@TMP@@%'").fetchone()[0]
    return len(cambios), quedan


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="escribe los cambios en la DB")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--raiz", default=RAIZ)
    ap.add_argument("--test", action="store_true", help="autochequeo y sale")
    args = ap.parse_args()

    if args.test:
        return autochequeo()

    t0 = time.time()
    print(f"Indexando {args.raiz} (solo metadata, no lee contenido)...")
    idx, n = indexar_disco(args.raiz)
    print(f"  {n:,} imagenes en disco, {len(idx):,} claves (nombre,bytes)  [{time.time()-t0:.0f}s]")

    con = sqlite3.connect(args.db)
    filas = con.execute(
        "select id, file_path, bytes, file_hash_sha256 from files where borrado=0"
    ).fetchall()
    print(f"  {len(filas):,} filas vivas en la DB\n")

    print("Resolviendo (el costo es hashear los ambiguos)...")
    cambios, st = resolver(filas, idx, progreso=True)

    print("\nRESULTADO")
    print(f"  ya apuntaban bien      {st['ya_ok']:>8,}")
    print(f"  reapuntar por nombre   {st['por_nombre_tamano']:>8,}")
    print(f"  reapuntar por hash     {st['por_hash']:>8,}")
    print(f"  ambiguos (sin tocar)   {st['ambiguo']:>8,}")
    print(f"  solo existe el derivado{st['solo_derivado']:>8,}")
    print(f"  no encontrados         {st['no_encontrado']:>8,}")
    print(f"  --> a escribir         {len(cambios):>8,}")

    if cambios:
        print("\n  ejemplos:")
        for _, viejo, nuevo in cambios[:3]:
            print(f"    {viejo}\n      -> {nuevo}")

    if not args.aplicar:
        print("\nSIMULACION: no se escribio nada. Repeti con --aplicar.")
        return 0

    escritas, quedan = escribir(con, cambios)
    print(f"\nESCRITO: {escritas:,} rutas actualizadas en {args.db}")
    print(f"  filas aparcadas sin resolver: {quedan}  (tiene que ser 0)")
    return 0


def autochequeo():
    """Chequea la logica de resolver() sin tocar disco ni DB."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        a = os.path.join(d, "vive.jpg")
        with open(a, "wb") as f:
            f.write(b"x" * 10)
        movido = os.path.join(d, "movido.jpg")
        with open(movido, "wb") as f:
            f.write(b"y" * 20)

        idx = {("movido.jpg", 20): [movido], ("vive.jpg", 10): [a]}
        filas = [
            (1, a, 10, None),                                  # existe -> intacto
            (2, os.path.join(d, "viejo", "movido.jpg"), 20, None),  # unico -> reapunta
            (3, os.path.join(d, "viejo", "fantasma.jpg"), 99, None),  # nada -> no encontrado
        ]
        cambios, st = resolver(filas, idx)
        assert st["ya_ok"] == 1, st
        assert st["por_nombre_tamano"] == 1, st
        assert st["no_encontrado"] == 1, st
        assert cambios == [(2, filas[1][1], movido)], cambios

        # un mismo archivo no puede ser reclamado dos veces
        filas2 = [(4, os.path.join(d, "a", "movido.jpg"), 20, None),
                  (5, os.path.join(d, "b", "movido.jpg"), 20, None)]
        cambios2, st2 = resolver(filas2, idx)
        assert len(cambios2) == 1 and st2["no_encontrado"] == 1, (cambios2, st2)

        # ROBO: la fila 7 ya ocupa 'vive.jpg'. La 6, procesada antes, no debe
        # quitarselo aunque el (nombre, bytes) le calce.
        filas3 = [(6, os.path.join(d, "viejo", "vive.jpg"), 10, None), (7, a, 10, None)]
        cambios3, st3 = resolver(filas3, idx)
        assert cambios3 == [], cambios3
        assert st3["ya_ok"] == 1 and st3["no_encontrado"] == 1, st3

        # DERIVADO: existiendo original y copia del export, gana el original.
        der = os.path.join(d, "Fotos_Organizadas_IA_v3", "copia.jpg")
        orig = os.path.join(d, "originales", "copia.jpg")
        for q in (der, orig):
            os.makedirs(os.path.dirname(q), exist_ok=True)
            with open(q, "wb") as f:
                f.write(b"z" * 30)
        # Ambas copias son identicas, asi que el hash calza con las dos: el
        # desempate lo hace el orden, y ahi es donde el original tiene que ganar.
        idx4 = {("copia.jpg", 30): [der, orig]}
        cambios4, st4 = resolver([(8, der, 30, sha256(der))], idx4)
        assert cambios4 and cambios4[0][2] == orig, cambios4
        assert st4["solo_derivado"] == 0, st4
        # sin original, se acepta el derivado como ultimo recurso
        cambios5, st5 = resolver([(9, der, 30, None)], {("copia.jpg", 30): [der]})
        assert st5["ya_ok"] == 1 and cambios5 == [], (st5, cambios5)

    # ESCRITURA: intercambio de rutas bajo UNIQUE. Es el caso que reventaba
    # antes de escribir en dos fases.
    con = sqlite3.connect(":memory:")
    con.execute("create table files (id integer primary key, file_path text unique not null)")
    con.executemany("insert into files values (?,?)", [(1, "P1"), (2, "P2")])
    con.commit()
    n, quedan = escribir(con, [(1, "P1", "P2"), (2, "P2", "P1")])
    assert (n, quedan) == (2, 0), (n, quedan)
    assert con.execute("select file_path from files where id=1").fetchone()[0] == "P2"
    assert con.execute("select file_path from files where id=2").fetchone()[0] == "P1"

    # destinos repetidos: aborta y no escribe
    try:
        escribir(con, [(1, "P2", "X"), (2, "P1", "X")])
        raise AssertionError("deberia haber abortado por destino repetido")
    except ValueError:
        pass

    print("autochequeo OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

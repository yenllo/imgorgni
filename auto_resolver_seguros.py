"""Marca automaticamente como 'descartar' los pares de duplicados en los que
confiamos sin supervision: EXACTO (mismo SHA256, sin ambiguedad posible) y
RECOMPRIMIDO (pHash<=2 Y mismo numero de serie de camara en el nombre, ver
el fix en 5_duplicados.py). NO toca SIMILAR ni "ambos corruptos": ahi vimos
que hay fotos genuinamente distintas mezcladas, necesitan ojo humano.

Pensado para correr desatendido via el Programador de tareas de Windows
mientras el usuario no esta. Por diseno SOLO marca decision='descartar':
no mueve ningun archivo a cuarentena (eso lo dispara el usuario a mano
desde la web cuando vuelva, con el boton de barra de progreso).

    python auto_resolver_seguros.py
"""
import datetime
import os
import sqlite3
from pathlib import Path

CARPETA = Path(__file__).resolve().parent
DB = "C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
LOG = str(CARPETA / "auto_resolver_seguros.log")
BACKUP = Path(r"K:\_db\photos_v2_backup.db")


def respaldar(conn):
    """Refresca el backup de la DB en el 8TB. Devuelve texto para el log.

    Usa conn.backup() y no una copia de archivo: la DB puede estar en uso
    (la web curadora) y una copia cruda saldria inconsistente. Escribe a un
    .tmp y recien ahi reemplaza, para no quedarse sin backup si falla a mitad.
    """
    if not BACKUP.parent.is_dir():
        return "backup salteado (K: no conectado)"
    tmp = BACKUP.with_suffix(".tmp")
    try:
        dst = sqlite3.connect(tmp)
        try:
            conn.backup(dst)
        finally:
            dst.close()  # en Windows os.replace falla si el handle sigue abierto
        os.replace(tmp, BACKUP)
        return f"backup ok ({BACKUP.stat().st_size / 2**30:.2f} GB)"
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return f"backup FALLO: {e}"


def main():
    conn = sqlite3.connect(DB, timeout=60)

    # EXACTO: siempre seguro, mismo archivo byte a byte.
    n_exacto = conn.execute("""
        UPDATE duplicados SET decision='descartar'
        WHERE tipo='EXACTO' AND decision='pendiente'
    """).rowcount

    # RECOMPRIMIDO: ya viene filtrado por 5_duplicados.py (score<=2 y mismo
    # numero de camara), asi que confiar en el tipo alcanza.
    n_recomp = conn.execute("""
        UPDATE duplicados SET decision='descartar'
        WHERE tipo='RECOMPRIMIDO' AND decision='pendiente'
    """).rowcount

    conn.commit()
    estado_backup = respaldar(conn)
    conn.close()

    linea = (f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
             f"EXACTO marcados: {n_exacto}  RECOMPRIMIDO marcados: {n_recomp}  "
             f"{estado_backup}\n")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linea)
    print(linea, end="")


if __name__ == "__main__":
    main()

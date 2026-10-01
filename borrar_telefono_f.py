"""Borra del disco (y de la base) los archivos que quedaron en F:\\Telefonos_y_Capturas.
Necesita correr en una terminal elevada (Administrador): los dueños de esos
archivos son de otra instalación de Windows, y el usuario actual solo tiene
Escritura+Lectura ahí, no Borrado -- Administradores sí tiene control total.

    python borrar_telefono_f.py
"""
import os
import sqlite3

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"


def main():
    c = sqlite3.connect(DB, timeout=30)
    filas = c.execute("SELECT id, file_path FROM files WHERE file_path LIKE 'F:%'").fetchall()
    print(f"a procesar: {len(filas)}")

    borrados = ya_no_estaba = fallos = 0
    for id_, fp in filas:
        try:
            if os.path.exists(fp):
                os.remove(fp)
                borrados += 1
            else:
                ya_no_estaba += 1
            c.execute("DELETE FROM event_photos WHERE photo_id=?", (id_,))
            c.execute("DELETE FROM photo_features WHERE photo_id=?", (id_,))
            c.execute("DELETE FROM files WHERE id=?", (id_,))
        except Exception as e:
            fallos += 1
            print(f"  fallo #{id_} {fp}: {e}")
    c.commit()
    print(f"\nborrados del disco de verdad : {borrados}")
    print(f"ya no estaban (sin espacio)  : {ya_no_estaba}")
    print(f"fallos                       : {fallos}")
    c.close()


if __name__ == "__main__":
    main()

"""Los tests importan los modulos de la raiz (db, utils_*, 5_duplicados...).

Al mudarlos de la raiz a tests/ dejaron de verlos, porque Python agrega al
sys.path la carpeta DEL test, no la del proyecto. Esto lo devuelve.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
# tambien mantenimiento/: hay tests de verificar_raw, verificar_integridad y
# refrescar_hashes, que ahora viven ahi
for _p in (RAIZ, RAIZ / "mantenimiento"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

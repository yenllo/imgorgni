"""Compara E: contra el inventario de K: guardado antes (inventario_K.txt),
sin necesitar los dos discos conectados a la vez. Borra de E: solo lo que
figura en el inventario con el mismo tamano. Lo que no esta en el
inventario (o no coincide el tamano) queda intacto en E:.

    python verificar_y_borrar_E_vs_inventario.py
"""
from pathlib import Path

ORIGEN = Path("E:/")
INVENTARIO = Path("inventario_K.txt")

print("cargando inventario de K:...", flush=True)
tam_por_ruta = {}
for linea in INVENTARIO.read_text(encoding="utf-8", errors="ignore").splitlines():
    size_str, rel = linea.split("\t", 1)
    tam_por_ruta[rel] = int(size_str)
print(f"{len(tam_por_ruta):,} archivos en el inventario", flush=True)

verificados = 0
borrados = 0
no_coincide = []
no_en_k = 0

print("recorriendo E:...", flush=True)
for p in ORIGEN.rglob("*"):
    if not p.is_file():
        continue
    rel = str(p.relative_to(ORIGEN))
    tam_k = tam_por_ruta.get(rel)
    if tam_k is None:
        no_en_k += 1
        continue
    verificados += 1
    if p.stat().st_size == tam_k:
        p.unlink()
        borrados += 1
    else:
        no_coincide.append(rel)
    if verificados % 5000 == 0:
        print(f"  {verificados:,} verificados, {borrados:,} borrados", flush=True)

print(f"\nlisto:")
print(f"  en inventario de K: y verificados: {verificados:,}")
print(f"  borrados de E: (tamano OK): {borrados:,}")
print(f"  tamano no coincide (NO borrados): {len(no_coincide):,}")
print(f"  no estan en K: (intactos en E:): {no_en_k:,}")

if no_coincide:
    Path("no_coincide_E_vs_K.txt").write_text("\n".join(no_coincide), encoding="utf-8")
    print("  lista en no_coincide_E_vs_K.txt")

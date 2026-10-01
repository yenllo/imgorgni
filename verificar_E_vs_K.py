"""Compara cada archivo de E: contra su equivalente en K: (misma ruta
relativa) por tamaño. Sirve para confirmar que lo que quedo en K: es
realmente lo mismo que hay en E:, no solo coincidencia de tamano+fecha
como uso robocopy internamente.

    python verificar_E_vs_K.py
"""
from pathlib import Path

ORIGEN = Path("E:/")
DESTINO = Path("K:/")

coinciden = 0
faltan_en_k = []
tamano_distinto = []

print("recorriendo E:...", flush=True)
total = 0
for p in ORIGEN.rglob("*"):
    if not p.is_file():
        continue
    total += 1
    rel = p.relative_to(ORIGEN)
    destino = DESTINO / rel
    if not destino.exists():
        faltan_en_k.append(str(rel))
        continue
    if destino.stat().st_size == p.stat().st_size:
        coinciden += 1
    else:
        tamano_distinto.append(str(rel))
    if total % 5000 == 0:
        print(f"  {total:,} revisados", flush=True)

print(f"\ntotal en E:: {total:,}")
print(f"coinciden en K: (mismo tamano): {coinciden:,}")
print(f"faltan en K: (no llegaron): {len(faltan_en_k):,}")
print(f"tamano distinto (sospechoso): {len(tamano_distinto):,}")

if faltan_en_k:
    Path("faltan_en_K.txt").write_text("\n".join(faltan_en_k), encoding="utf-8")
    print("lista en faltan_en_K.txt")
if tamano_distinto:
    Path("tamano_distinto_E_K.txt").write_text("\n".join(tamano_distinto), encoding="utf-8")
    print("lista en tamano_distinto_E_K.txt")

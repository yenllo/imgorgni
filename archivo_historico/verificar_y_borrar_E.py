"""Para cada archivo de E: que ya llego a la balsa (G:\\_ferry_E o
C:\\_ferry_E), verifica que el tamaño coincida y borra el original de
E:. Lo que todavia no esta en la balsa (pendiente) no se toca.

    python verificar_y_borrar_E.py
"""
from pathlib import Path

ORIGEN = Path("E:/")
PUENTES = [Path(r"G:\_ferry_E"), Path(r"C:\_ferry_E")]

verificados = 0
borrados = 0
no_coincide = []
no_en_balsa = 0

print("recorriendo E:...", flush=True)
for i, p in enumerate(ORIGEN.rglob("*"), 1):
    if not p.is_file():
        continue
    rel = p.relative_to(ORIGEN)
    encontrado = None
    for puente in PUENTES:
        candidato = puente / rel
        if candidato.exists():
            encontrado = candidato
            break
    if encontrado is None:
        no_en_balsa += 1
        continue

    verificados += 1
    if encontrado.stat().st_size == p.stat().st_size:
        p.unlink()
        borrados += 1
    else:
        no_coincide.append(str(rel))

    if verificados % 5000 == 0:
        print(f"  {verificados:,} verificados, {borrados:,} borrados de E:", flush=True)

print(f"\nlisto:")
print(f"  en balsa y verificados: {verificados:,}")
print(f"  borrados de E: (tamaño OK): {borrados:,}")
print(f"  tamaño no coincide (NO borrados, revisar): {len(no_coincide):,}")
print(f"  todavia no en balsa (intactos en E:): {no_en_balsa:,}")

if no_coincide:
    Path("no_coincide_E.txt").write_text("\n".join(no_coincide), encoding="utf-8")
    print("  lista de no-coincidencias en no_coincide_E.txt")

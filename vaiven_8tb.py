"""Vaiven E: -> 8TB usando G: y C: como balsa intermedia, porque E: (HDD
3.5") y el 8TB (tambien 3.5") comparten la unica bahia del dock y nunca
pueden estar conectados los dos a la vez.

Se corre el MISMO comando cada vez, sin importar que este conectado: mira
que hay disponible y decide solo.

  - E: conectado (el 8TB no)  -> modo CARGAR: copia el siguiente lote de
    E: a "_ferry_E" en G: y/o C: (lo que entre segun espacio libre). No
    borra nada de E:, solo copia -- el original queda intacto hasta que
    decidas borrarlo aparte, ya verificado en el 8TB.
  - el 8TB conectado           -> modo DRENAR: mueve lo que haya juntado
    la balsa (G:/C:) al 8TB, respetando la misma ruta relativa, y lo
    marca como hecho.

Resumible: ferry_hecho.txt guarda que archivos ya llegaron de verdad al
8TB. Lo que este a medio camino en la balsa se lee directo del disco, no
hace falta otro checkpoint para eso.

No incluye Fotos_F_rescatadas de C:/G: (esas van directo al 8TB en cuanto
se conecte, sin pasar por la balsa -- ese es un robocopy aparte).

    python vaiven_8tb.py <letra_8tb>
"""
import shutil
import sys
from pathlib import Path

ORIGEN = Path("E:/")
PUENTES = [Path(r"C:\_ferry_E")]
HECHO = Path("ferry_hecho.txt")
MARGEN_GB = 5  # dejar libre en el disco puente, no llenarlo del todo


def espacio_libre_gb(drive_root: str) -> float:
    try:
        return shutil.disk_usage(drive_root).free / 1024**3
    except OSError:
        return -1  # disco no conectado


def ya_hechos() -> set:
    if not HECHO.exists():
        return set()
    return set(HECHO.read_text(encoding="utf-8", errors="ignore").splitlines())


def modo_cargar():
    hechos = ya_hechos()
    en_puente = {str(p.relative_to(puente))
                 for puente in PUENTES if puente.exists()
                 for p in puente.rglob("*") if p.is_file()}

    print("listando E:...", flush=True)
    todos = [p for p in ORIGEN.rglob("*") if p.is_file()]
    pendientes = [p for p in todos
                  if str(p.relative_to(ORIGEN)) not in hechos
                  and str(p.relative_to(ORIGEN)) not in en_puente]
    print(f"total E:: {len(todos):,}  ya migrados: {len(hechos):,}  "
          f"en balsa sin drenar: {len(en_puente):,}  pendientes: {len(pendientes):,}",
          flush=True)

    copiados = 0
    i = 0
    for p in pendientes:
        tam_gb = p.stat().st_size / 1024**3
        destino = None
        for _ in range(len(PUENTES)):
            candidato = PUENTES[i % len(PUENTES)]
            i += 1
            libre = espacio_libre_gb(candidato.drive + "\\")
            if libre >= 0 and libre - tam_gb > MARGEN_GB:
                destino = candidato
                break
        if destino is None:
            print(f"balsa llena (o sin disco puente conectado), "
                  f"pare en {copiados:,} archivos", flush=True)
            break
        rel = p.relative_to(ORIGEN)
        dest_path = destino / rel
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest_path)
        copiados += 1
        if copiados % 200 == 0:
            print(f"  {copiados:,} copiados a la balsa", flush=True)

    print(f"listo modo CARGAR: {copiados:,} archivos subidos a la balsa (G:/C:)")


def modo_drenar(letra_8tb: str):
    destino_raiz = Path(f"{letra_8tb}:\\")
    movidos = 0
    with open(HECHO, "a", encoding="utf-8") as hecho_f:
        for puente in PUENTES:
            if not puente.exists():
                continue
            for p in list(puente.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(puente)
                destino = destino_raiz / rel
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(destino))
                hecho_f.write(str(rel) + "\n")
                movidos += 1
                if movidos % 200 == 0:
                    hecho_f.flush()
                    print(f"  {movidos:,} drenados al 8TB", flush=True)

    for puente in PUENTES:
        if puente.exists():
            for d in sorted(puente.rglob("*"), reverse=True):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()

    print(f"listo modo DRENAR: {movidos:,} archivos movidos al 8TB")


def main():
    if len(sys.argv) < 2:
        print("uso: python vaiven_8tb.py <letra_8tb>")
        return
    letra = sys.argv[1].rstrip(":")

    tb_conectado = Path(f"{letra}:\\").exists()
    e_conectado = ORIGEN.exists()

    if tb_conectado:
        modo_drenar(letra)
    elif e_conectado:
        modo_cargar()
    else:
        print(f"no encuentro ni E: ni {letra}: conectados, nada para hacer")


if __name__ == "__main__":
    main()

"""Telefono -> notebook -> K:, sin perder nada en el camino (13/11/2026).

    python mantenimiento/telefono_a_k.py bajar [--esperar-hasta 7]   # telefono por USB (adb)
    python mantenimiento/telefono_a_k.py subir                       # en la casa: notebook -> K:
    python mantenimiento/telefono_a_k.py estado

Regla unica: un archivo se borra de un lado SOLO despues de comparar su SHA-256 en el otro.
- bajar: limpia la cache de apps; los RAW de "Mover fotos" que photos_v2.db ya tiene en K: (mismo
  SHA-256, no borrados, y el archivo sigue en el disco) se borran del telefono sin copiarlos; el resto
  (DCIM/Camera, WhatsApp Video/Images/Documents, RAW que faltan) se baja al notebook, se compara el
  hash del telefono con el del notebook y recien ahi se borra del telefono. Al final intenta subir.
- subir: solo por K: (la red de la casa, nunca Tailscale). Copia, relee el hash en K: y recien ahi
  borra la copia del notebook.
El manifiesto (_salidas_locales/telefono/manifiesto.json) hace todo reanudable.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
import db  # noqa: E402  verificar_disco_montado

ADB = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
LOCAL = _ROOT / "_salidas_locales" / "telefono"
MANIFIESTO = LOCAL / "manifiesto.json"
BASE = _ROOT / "photos_v2.db"
LIBRE_MIN = 20 * 1024 ** 3  # no llenar el notebook: para y deja el resto en el telefono

WA = "/sdcard/Android/media/com.whatsapp/WhatsApp/Media"
MOVER = [  # (carpeta del telefono, destino relativo en K:) — fotos de telefono: fuera del proyecto
    ("/sdcard/DCIM/Camera", "_Telefono_respaldo/DCIM_Camera"),
    (f"{WA}/WhatsApp Video", "_Telefono_respaldo/WhatsApp/Video"),
    (f"{WA}/WhatsApp Images", "_Telefono_respaldo/WhatsApp/Images"),
    (f"{WA}/WhatsApp Documents", "_Telefono_respaldo/WhatsApp/Documents"),
]
RAW_TEL = ["/sdcard/Mover fotos ", "/sdcard/Mover fotos"]  # RAW de camara (ojo: el primero con espacio)
RAW_DESTINO = "Fotos/AOrdenar/Mover_fotos_telefono"       # estos si son del proyecto


def log(msg):
    LOCAL.mkdir(parents=True, exist_ok=True)
    linea = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with open(LOCAL / "registro.log", "a", encoding="utf-8") as f:
        f.write(linea + "\n")
    print(linea, flush=True)


def adb(*args):
    return subprocess.run([str(ADB), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=0x08000000)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sanear(rel):
    """Ruta del telefono -> ruta valida en Windows (sin <>:"|?* ni espacios/puntos al final)."""
    partes = []
    for p in PurePosixPath(rel).parts:
        p = "".join("_" if c in '<>:"|?*\\' else c for c in p).rstrip(" .")
        partes.append(p or "_")
    return Path(*partes)


def cargar():
    try:
        return json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    except OSError:
        return {}


def guardar(man):
    LOCAL.mkdir(parents=True, exist_ok=True)
    tmp = MANIFIESTO.with_suffix(".tmp")
    tmp.write_text(json.dumps(man, ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(tmp, MANIFIESTO)


def libre_tel():
    r = adb("shell", "df -k /data | tail -1").stdout.split()
    return f"{int(r[3]) / 1024 ** 2:.1f} GB libres" if len(r) > 3 and r[3].isdigit() else "?"


def hashes(raiz):
    """{ruta en el telefono: sha256} de todo lo que cuelga de *raiz*, calculado en el telefono."""
    r = adb("shell", f"find '{raiz}' -type f -exec sha256sum {{}} +")
    out = {}
    for l in r.stdout.splitlines():
        h, _, p = l.partition("  ")
        if len(h) == 64 and p and not PurePosixPath(p).name.startswith("."):  # .nomedia y cia se quedan
            out[p] = h.lower()
    return out


def borrar_del_telefono(tels, man):
    """Borra del telefono SOLO rutas ya verificadas y marca las que de verdad desaparecieron."""
    if not tels:
        return
    lista = LOCAL / "_borrar.txt"
    lista.write_text("".join(t + "\n" for t in tels), encoding="utf-8", newline="\n")
    adb("push", str(lista), "/data/local/tmp/imgorgni_borrar.txt")
    r = adb("shell", "L=/data/local/tmp/imgorgni_borrar.txt; "
                     'while IFS= read -r f; do rm -f -- "$f"; done < $L; '
                     'while IFS= read -r f; do [ -e "$f" ] && echo "$f"; done < $L; rm -f $L')
    quedan = set(r.stdout.splitlines())
    for t in tels:
        if t not in quedan:
            man[t]["tel_borrado"] = True
    guardar(man)
    log(f"  borrados del telefono: {len(tels) - len(quedan)}" + (f", NO se pudieron {len(quedan)}" if quedan else ""))


def procesar(hs, raiz, destino, man):
    """Baja, verifica y borra del telefono. *hs*: {ruta tel: sha256}."""
    LOCAL.mkdir(parents=True, exist_ok=True)  # disk_usage necesita que exista
    verificados, bajados, malos = [], 0, 0
    for tel, h in sorted(hs.items()):
        e = man.get(tel)
        if e and e["sha"] == h and e["tel_borrado"]:
            continue
        ya = e and e["sha"] == h and (e["en_k"] or (e["local"] and Path(e["local"]).exists()))
        if not ya:
            if shutil.disk_usage(LOCAL).free < LIBRE_MIN:
                log("  el notebook quedo con menos de 20 GB libres: paro, el resto sigue en el telefono")
                break
            rel = sanear(PurePosixPath(tel).relative_to(raiz))
            local = LOCAL / "datos" / destino / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            r = adb("pull", "-a", tel, str(local))
            if r.returncode or not local.exists() or sha256(local) != h:
                log(f"  no cuadra, queda en el telefono: {tel} {r.stderr.strip()[:120]}")
                local.unlink(missing_ok=True)
                malos += 1
                continue
            man[tel] = e = {"sha": h, "local": str(local), "k": f"{destino}/{rel.as_posix()}",
                            "tel_borrado": False, "en_k": False}
            bajados += 1
            if bajados % 200 == 0:
                guardar(man)
        verificados.append(tel)
    guardar(man)
    log(f"  {raiz}: {bajados} bajados y verificados, {malos} con problema")
    borrar_del_telefono(verificados, man)


def nas_para_leer():
    """Raiz montada de verdad para COMPROBAR que algo existe (leer, no subir): K: o Z:."""
    for raiz in ("K:\\", "Z:\\"):
        try:
            db.verificar_disco_montado(raiz)
            return raiz
        except db.DiscoCaido:
            continue
    return None


def rutas_en_base(shas):
    c = sqlite3.connect(f"file:{BASE}?mode=ro", uri=True)
    out, shas = {}, list(shas)
    for i in range(0, len(shas), 500):
        lote = shas[i:i + 500]
        q = ("SELECT lower(file_hash_sha256), file_path FROM files WHERE IFNULL(borrado,0)=0 "
             f"AND lower(file_hash_sha256) IN ({','.join('?' * len(lote))})")
        out.update(c.execute(q, lote).fetchall())
    return out


def raw_de_mover_fotos(man):
    nas = nas_para_leer()
    log(f"Mover fotos: compruebo en {nas or 'ningun NAS (los que ya estan en K: quedan en el telefono)'}")
    for i, raiz in enumerate(RAW_TEL, 1):
        hs = hashes(raiz)
        if not hs:
            continue
        en_base = rutas_en_base(set(hs.values()))
        borrar, faltan, sin_comprobar = [], {}, 0
        for tel, h in hs.items():
            ruta = en_base.get(h)
            if ruta is None:
                faltan[tel] = h
            elif nas and os.path.exists(nas + ruta[3:]):  # K:\Fotos\... -> <nas>Fotos\...
                man[tel] = {"sha": h, "local": None, "k": ruta, "tel_borrado": False, "en_k": True}
                borrar.append(tel)
            else:
                sin_comprobar += 1
        log(f"  {raiz!r}: {len(borrar)} ya en K: (mismo SHA-256), {len(faltan)} faltan, {sin_comprobar} sin comprobar")
        borrar_del_telefono(borrar, man)
        procesar(faltan, raiz, f"{RAW_DESTINO}/Mover fotos {i}", man)


def esperar_telefono(hasta_h):
    while True:
        if adb("get-state").stdout.strip() == "device":
            return True
        if hasta_h is None or datetime.now().hour >= hasta_h:
            return False
        time.sleep(60)


def bajar(hasta_h=None):
    if not esperar_telefono(hasta_h):
        log("bajar: no hay telefono conectado por adb")
        return
    log(f"bajar: telefono con {libre_tel()}")
    adb("shell", "pm", "trim-caches", "999999999999")
    log(f"cache de apps limpiada: {libre_tel()}")
    man = cargar()
    raw_de_mover_fotos(man)
    for raiz, destino in MOVER:
        procesar(hashes(raiz), raiz, destino, man)
    adb("shell", "content", "call", "--uri", "content://media", "--method", "scan_volume",
        "--arg", "external_primary")  # que la galeria se entere de lo borrado
    log(f"bajar: listo, telefono con {libre_tel()}")
    try:
        subir()
    except db.DiscoCaido as e:
        log(f"subir: K: no esta ({e}); queda en el notebook hasta que estes en la casa")


def subir(raiz="K:\\"):
    db.verificar_disco_montado(raiz)  # solo K: (red de la casa); afuera corta aca
    man, n = cargar(), 0
    for e in man.values():
        if e["en_k"] or not e["local"] or not Path(e["local"]).exists():
            continue
        local, dest = Path(e["local"]), Path(raiz) / e["k"]
        while dest.exists() and sha256(dest) != e["sha"]:  # otro archivo con el mismo nombre
            dest = dest.with_stem(dest.stem + "_tel")
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, dest)
            if sha256(dest) != e["sha"]:
                log(f"subir: el hash no cuadra en K:, queda en el notebook: {local}")
                dest.unlink(missing_ok=True)
                continue
        e["k"], e["en_k"] = dest.relative_to(raiz).as_posix(), True
        local.unlink()
        n += 1
        if n % 100 == 0:
            guardar(man)
    guardar(man)
    for d in sorted((LOCAL / "datos").rglob("*"), reverse=True) if (LOCAL / "datos").exists() else []:
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    log(f"subir: {n} archivos a K: verificados y borrados del notebook")


def estado():
    man = cargar()
    loc = [e for e in man.values() if not e["en_k"] and e["local"] and Path(e["local"]).exists()]
    print(f"{len(man)} archivos en el manifiesto; borrados del telefono {sum(e['tel_borrado'] for e in man.values())}; "
          f"en K: {sum(e['en_k'] for e in man.values())}; esperando en el notebook {len(loc)} "
          f"({sum(Path(e['local']).stat().st_size for e in loc) / 1e9:.1f} GB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["bajar", "subir", "estado"])
    ap.add_argument("--esperar-hasta", type=int, help="hora (0-23) hasta la que esperar el telefono")
    a = ap.parse_args()
    if a.cmd == "estado":
        return estado()
    import ctypes  # bajar y subir reescriben el manifiesto: nunca dos a la vez
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    candado = k32.CreateMutexW(None, False, "Local\\imgorgni_telefono_a_k")  # noqa: F841 vive con el proceso
    if ctypes.get_last_error() == 183:
        log(f"{a.cmd}: ya hay otra corrida en curso; salgo")
        return
    if a.cmd == "bajar":
        bajar(a.esperar_hasta)
    else:
        try:
            subir()
        except db.DiscoCaido as e:
            log(f"subir: K: no esta ({e}); nada que hacer hasta que estes en la casa")


if __name__ == "__main__":
    main()

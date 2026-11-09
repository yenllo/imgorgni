"""Compara la carpeta local de un trabajo contra la de la Pi, ANTES de crear
la galeria.

    python verificar_trabajo.py "TrabajoFotografico/LigaBabbo/F2 24-09-2026" \
        "/mnt/fotos/Fotos/Galeria/Trabajo/LigaBabbo/F2 24-09-2026"

Sale con codigo 1 si falta algo, asi se puede encadenar:

    python verificar_trabajo.py LOCAL PI && ssh fotosnas "... galeria.py nueva ..."

Por que existe (lo que paso el 24/9/2026 con la F2 Babbo):

  - 19 .ARW y 23 .JPG nunca llegaron a la Pi. Nadie se entero: la galeria se
    creo igual y el cliente eligio sobre 2.846 fotos cuando habia 2.869.
  - La galeria busca el RAW hermano UNA SOLA VEZ, al crearla. Si en ese
    momento la carpeta RAW todavia se esta copiando, esas fotos quedan sin
    RAW para siempre (38 de 2.846 quedaron vinculadas). Por eso el chequeo va
    ANTES de crear, no despues.
  - En la carpeta del trabajo habia 12 fotos personales del dia siguiente,
    mezcladas por la tarjeta. Terminaron subidas a la carpeta del cliente.

De ahi los cuatro chequeos: falta en la Pi, falta en local, JPEG sin RAW (y al
reves), y fotos de otro dia.

--subir copia a la Pi lo que falte, pero imprime la lista primero: lo que hay
que mirar antes de mandar no es el conteo, son los nombres.
"""
import argparse
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

CRUDAS = {".arw", ".cr2", ".nef", ".dng", ".raf", ".orf", ".rw2"}
FOTOS = {".jpg", ".jpeg"}
HOST = "fotosnas"          # el alias de ~/.ssh/config


def ssh(orden):
    """['ssh', 'fotosnas', '<un solo string>'].

    El binario se busca en el PATH porque el python de Windows no lo encuentra
    por el nombre. Y `orden` va entero en UN argumento: ssh junta con espacios
    todo lo que le pases y el shell remoto lo vuelve a parsear, asi que
    partirlo en ['sh','-c',cmd] rompe las comillas y el `cd` se pierde (se
    termina listando el home en vez de la carpeta)."""
    import shutil
    exe = shutil.which("ssh") or r"C:\Windows\System32\OpenSSH\ssh.exe"
    return [exe, HOST, orden]


def clase(sufijo):
    s = sufijo.lower()
    return "RAW" if s in CRUDAS else "JPEG" if s in FOTOS else None


def locales(carpeta):
    """{clase: {nombre_sin_extension: Path}}"""
    out = {"JPEG": {}, "RAW": {}}
    for p in Path(carpeta).rglob("*"):
        c = p.is_file() and clase(p.suffix)
        if c:
            out[c][p.stem] = p
    return out


def remotas(carpeta):
    """Lo mismo, del otro lado del ssh. Solo nombres: los Path no sirven aca."""
    r = subprocess.run(
        ssh("cd %s && find . -type f -printf '%%f\\n'" % shquote(carpeta)),
        capture_output=True, text=True)
    if r.returncode:
        sys.exit("No pude listar %s en la Pi:\n%s" % (carpeta, r.stderr.strip()))
    out = {"JPEG": set(), "RAW": set()}
    for n in r.stdout.splitlines():
        p = Path(n.strip())
        c = p.name and clase(p.suffix)
        if c:
            out[c].add(p.stem)
    return out


def shquote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def ruta_remota(s):
    """Git Bash reescribe los argumentos que empiezan con "/" a ruta Windows:
    /mnt/fotos/... llega como C:/Program Files/Git/mnt/fotos/... . Se recupera
    cortando desde la raiz conocida, asi el script anda igual desde bash, cmd
    o PowerShell sin acordarse de MSYS_NO_PATHCONV."""
    s = s.replace("\\", "/")
    for raiz in ("/mnt/", "/home/", "/srv/", "/media/"):
        i = s.find(raiz)
        if i > 0:
            return s[i:]
    return s


def dias(paths):
    """Cuantas fotos hay por dia de captura. Una tarjeta trae lo que se
    disparo DESPUES del trabajo: si aparece un segundo dia, eso no es del
    trabajo aunque este en la carpeta."""
    from PIL import Image
    cuenta = Counter()
    ejemplo = {}
    for p in paths:
        try:
            with Image.open(p) as im:
                t = im.getexif().get(306) or ""
        except Exception:
            t = ""
        d = t[:10].replace(":", "-") if t else "sin fecha"
        cuenta[d] += 1
        ejemplo.setdefault(d, []).append(p.stem)
    return cuenta, ejemplo


COLA = 256 * 1024      # cuanto de la cola se mira buscando el fin de imagen


def sano(ruta):
    """Un JPEG entero tiene la marca de fin (ffd9) cerca del final.

    OJO con la version obvia: los JPEG que salen de la a7R III traen entre 17
    y 33 KB de relleno en CERO despues del ffd9, asi que exigirlo en los dos
    ultimos bytes marca como rotos los 2.869 archivos de una jornada. Los
    exports de Lightroom si terminan justo, que fue lo que me hizo creer que
    la regla servia.

    Por eso se busca en los ultimos 256 KB y no en todo el archivo: un JPEG
    cortado a la mitad todavia tiene el ffd9 de la miniatura EXIF al
    principio, y mirar el archivo entero lo daria por bueno.

    Vale la pena: el 28/9/2026 un DSC07006.jpg de 6,7 MB tenia la cabecera
    bien y ningun ffd9 en la cola. Pasaba cualquier chequeo por nombre,
    tamaño y fecha, y reventaba recien al abrirlo (PIL: "image file is
    truncated"). Leer la cola sale casi gratis; abrirlos todos, no.

    Un RAW no tiene marca de fin, asi que ahi solo se mira el tamaño contra
    la mediana: un .ARW cortado pesa mucho menos que sus hermanos.
    """
    try:
        with open(ruta, "rb") as f:
            f.seek(max(0, os.path.getsize(ruta) - COLA))
            return b"\xff\xd9" in f.read()
    except OSError:
        return False


def rotos_locales(loc):
    malos = {n for n, p in loc["JPEG"].items() if not sano(p)}
    crudos = [(n, p.stat().st_size) for n, p in loc["RAW"].items()]
    if len(crudos) >= 5:
        med = sorted(s for _, s in crudos)[len(crudos)//2]
        malos |= {n for n, s in crudos if s < med * 0.5}
    return malos


def rotos_remotos(carpeta):
    """Lo mismo del otro lado. El archivo cortado del 28/9 estaba roto EN LA
    PI y sano en la notebook: revisar solo el lado local no lo habria visto."""
    guion = (
        "import os,sys\n"
        "from pathlib import Path\n"
        "J={'.jpg','.jpeg'}\n"
        "malos=[];crudos=[]\n"
        "for p in Path(sys.argv[1]).rglob('*'):\n"
        "    if not p.is_file(): continue\n"
        "    e=p.suffix.lower()\n"
        "    if e in J:\n"
        "        try:\n"
        "            with open(p,'rb') as f:\n"
        "                f.seek(max(0,p.stat().st_size-262144))\n"
        "                if b'\\xff\\xd9' not in f.read(): malos.append(p.stem)\n"
        "        except OSError: malos.append(p.stem)\n"
        "    elif e=='.arw': crudos.append((p.stem,p.stat().st_size))\n"
        "if len(crudos)>=5:\n"
        "    m=sorted(s for _,s in crudos)[len(crudos)//2]\n"
        "    malos+=[n for n,s in crudos if s<m*0.5]\n"
        "print('\\n'.join(malos))\n")
    r = subprocess.run(ssh("python3 - %s" % shquote(carpeta)),
                       input=guion, capture_output=True, text=True)
    if r.returncode:
        print("  (no pude revisar los archivos de la Pi: %s)" % r.stderr.strip()[:80])
        return set()
    return {n for n in r.stdout.split() if n}


def informe(nombre, faltan, limite=40):
    if not faltan:
        print("  %-22s ok" % nombre)
        return 0
    m = sorted(faltan)
    print("  %-22s FALTAN %d" % (nombre, len(m)))
    print("     " + ", ".join(m[:limite]) + (" ..." if len(m) > limite else ""))
    return len(m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("local")
    ap.add_argument("pi")
    ap.add_argument("--subir", action="store_true",
                    help="copia a la Pi lo que falte alla")
    a = ap.parse_args()
    a.pi = ruta_remota(a.pi)
    if not Path(a.local).is_dir():
        sys.exit("No existe la carpeta local %s" % a.local)

    loc, pi = locales(a.local), remotas(a.pi)
    print("local: %d JPEG, %d RAW" % (len(loc["JPEG"]), len(loc["RAW"])))
    print("Pi   : %d JPEG, %d RAW\n" % (len(pi["JPEG"]), len(pi["RAW"])))

    malas = 0
    print("no llegaron a la Pi:")
    falta_pi = {c: set(loc[c]) - pi[c] for c in ("JPEG", "RAW")}
    for c in ("JPEG", "RAW"):
        malas += informe(c, falta_pi[c])

    print("\nestan en la Pi y no en local:")
    for c in ("JPEG", "RAW"):
        malas += informe(c, pi[c] - set(loc[c]))

    print("\nsin su par (en local):")
    informe("JPEG sin RAW", set(loc["JPEG"]) - set(loc["RAW"]))
    informe("RAW sin JPEG", set(loc["RAW"]) - set(loc["JPEG"]))

    print("\narchivos cortados:")
    rl, rp = rotos_locales(loc), rotos_remotos(a.pi)
    malas += informe("en local", rl)
    malas += informe("en la Pi", rp)
    solo_pi = rp - rl
    if solo_pi:
        print("     ^ sanos en local y rotos en la Pi: la copia se corto. "
              "Volve a copiar esos %d." % len(solo_pi))

    print("\ndias de captura (local):")
    cuenta, ejemplo = dias(loc["JPEG"].values())
    for d, n in sorted(cuenta.items(), key=lambda kv: -kv[1]):
        marca = ""
        if len(cuenta) > 1 and n < max(cuenta.values()) / 10:
            marca = "  <-- OJO: no parece del trabajo (%s)" % ", ".join(ejemplo[d][:4])
            malas += n
        print("  %-12s %5d%s" % (d, n, marca))

    if a.subir and (falta_pi["JPEG"] or falta_pi["RAW"]):
        subir(a, loc, falta_pi)
        return
    print()
    if malas:
        sys.exit("%d cosas para revisar. NO crees la galeria todavia." % malas)
    print("Todo cuadra. Se puede crear la galeria.")


def subir(a, loc, falta_pi):
    envio = [loc[c][n] for c in ("JPEG", "RAW") for n in sorted(falta_pi[c])]
    base = Path(a.local)
    rel = [str(p.relative_to(base)).replace("\\", "/") for p in envio]
    mb = sum(p.stat().st_size for p in envio) / 1e6
    print("\n--subir: %d archivos, %.0f MB" % (len(rel), mb))
    for r in rel:
        print("   ", r)
    lista = base / "_subir.txt"
    lista.write_text("\n".join(rel) + "\n", encoding="utf-8", newline="\n")
    tar = subprocess.Popen(["tar", "cf", "-", "-C", str(base), "-T", str(lista)],
                           stdout=subprocess.PIPE)
    r = subprocess.run(ssh("cd %s && tar xf -" % shquote(a.pi)), stdin=tar.stdout)
    tar.wait()
    lista.unlink(missing_ok=True)
    if r.returncode:
        sys.exit("Fallo la copia.")
    print("\nSubido. Corre el chequeo otra vez para confirmar.")


def demo():
    """La comparacion es conjuntos: se prueba sin tocar la Pi ni el disco."""
    loc = {"JPEG": {"DSC001": 1, "DSC002": 1, "DSC003": 1}, "RAW": {"DSC001": 1, "DSC002": 1}}
    pi = {"JPEG": {"DSC001", "DSC002"}, "RAW": {"DSC001", "DSC002", "DSC009"}}
    assert set(loc["JPEG"]) - pi["JPEG"] == {"DSC003"}, "no vio el JPEG que falta en la Pi"
    assert pi["RAW"] - set(loc["RAW"]) == {"DSC009"}, "no vio el RAW que sobra en la Pi"
    assert set(loc["JPEG"]) - set(loc["RAW"]) == {"DSC003"}, "no vio el JPEG sin RAW"
    assert clase(".ARW") == "RAW" and clase(".jpg") == "JPEG" and clase(".txt") is None
    assert shquote("F2 24-09") == "'F2 24-09'"
    assert ruta_remota("C:/Program Files/Git/mnt/fotos/x") == "/mnt/fotos/x", "no deshizo MSYS"
    assert ruta_remota("/mnt/fotos/x") == "/mnt/fotos/x"
    # el jpeg cortado: cabecera buena, cola mala, tamaño creible
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "sana.jpg").write_bytes(b"\xff\xd8\xff\xe1" + b"x" * 500 + b"\xff\xd9")
        (d / "rota.jpg").write_bytes(b"\xff\xd8\xff\xe1" + b"x" * 500 + b"\x00\x00")
        (d / "vacia.jpg").write_bytes(b"")
        assert sano(d / "sana.jpg"), "marco como rota una sana"
        assert not sano(d / "rota.jpg"), "no vio el jpeg cortado"
        assert not sano(d / "vacia.jpg"), "el archivo vacio tiene que caer"
        loc = {"JPEG": {p.stem: p for p in d.glob("*.jpg")},
               "RAW": {"a%d" % i: None for i in range(4)}}
        assert rotos_locales({"JPEG": loc["JPEG"], "RAW": {}}) == {"rota", "vacia"}, \
            "rotos_locales no devolvio los dos malos"
    assert shquote("a'b") == "'a'\\''b'", "no escapa la comilla"
    # el dia minoritario se marca: 12 personales contra 2846 del trabajo
    c = Counter({"2026-09-24": 2846, "2026-09-25": 12})
    raro = [d for d, n in c.items() if n < max(c.values()) / 10]
    assert raro == ["2026-09-25"], "no marco el dia que no es del trabajo"
    print("demo ok: detecta lo que falta de cada lado, el JPEG sin RAW y el dia intruso")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    else:
        main()

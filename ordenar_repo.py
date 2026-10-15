#!/usr/bin/env python3
"""Ordena la raiz del repo: 445 archivos sueltos a carpetas con sentido.

Reglas que se respetan si o si:
  - lo que llama un lanzador (.bat/.ps1/.sh) NO se mueve: una ruta rota ahi no
    falla al importar, falla a las 3 de la manana cuando corre la tarea.
  - lo trackeado se mueve con `git mv`; lo que no, con un move comun.
  - a tests/ solo van archivos con `def test_` de verdad. Los tres de anoche
    trabajan al importarse (indexaron 198.907 archivos durante la coleccion)
    y revientan: van a los borradores.

    python ordenar_repo.py            # simulacro
    python ordenar_repo.py --hacerlo
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parent
HACER = "--hacerlo" in sys.argv

# ------------------------------------------------------------- que se queda
NUCLEO = {
    "main.py", "db.py", "app.py", "web_curador.py", "galeria.py", "galeria_pi.py",
    "calentar_local.py", "agente_calentado.py", "autocull_runner.py",
    "1_indexado.py", "2_analisis.py", "3_agrupacion.py", "3_agrupacion_mejorado.py",
    "3b_personas.py", "4_exportar.py", "5_duplicados.py",
    "seleccion_ig.py", "aprender_apuntes.py", "editar_renato.py", "edicion_ig.py",
    "setup_models.py", "precompute_scene_embeddings.py", "ordenar_repo.py",
}
CONFIGS_VIVOS = {"config_K.json", "config.json", "config_cloud.json",
                 "config_lightroom.json"}
DOCS_RAIZ = {"CLAUDE.md", "README.md"}

# ------------------------------------------------------- borradores de anoche
BORRADORES = {"test_fast_compare.py", "test_match.py", "test_name_size.py",
              "scratch_db.py", "audit_rapido.py", "check_db_paths.py",
              "check_galeria_db.py", "fase3_auditoria.py"}
# utiles de anoche que si valen
MANTENIMIENTO_EXTRA = {"auditar_nas.py", "indagar_db_y_links.py"}

MANTENIMIENTO = {
    "fase4_ejecucion_y_auditoria.py", "fase4_auditoria.py", "fase4_hash_completo.py",
    "fase4_verificar_muestra.py", "fase5_cierre.py", "fase5_desempatar.py",
    "fase5_mover.py", "fase5_reapuntar_db.py", "fase5_reporte.py",
    "fase6_quirurgico.py", "marcar_ausentes.py",
    "verificar_integridad.py", "verificar_raw.py", "verificar_exactos.py",
    "verificar_copia_mejor.py", "refrescar_hashes.py", "reclasificar_danados.py",
    "chequeo_superficie.py", "diagnostico_eventos.py", "rescatar_raw_ilegibles.py",
    "reubicar_por_hash.py", "verificar_tags.py", "generar_inventario_K.py",
} | MANTENIMIENTO_EXTRA

CHATARRA = {"app_backup.py", "app_broken.py", "app_broken_backup.py",
            "fix_indentation.py", "organizar_archivos.bat"}


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True, cwd=R)


def trackeados():
    return set(sh("git", "ls-files").stdout.splitlines())


def llamados_por_lanzadores():
    """Nombres de .py mencionados en algun .bat/.ps1/.sh de la raiz."""
    nombres = set()
    for p in list(R.glob("*.bat")) + list(R.glob("*.ps1")) + list(R.glob("*.sh")):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if p.name in CHATARRA:
            continue                      # un lanzador viejo no manda
        nombres |= set(re.findall(r"[A-Za-z0-9_]+\.py", t))
    return nombres


def es_test_real(p):
    try:
        return "def test_" in p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def plan():
    tk = trackeados()
    protegidos = llamados_por_lanzadores() | NUCLEO
    mov = {}                               # destino -> [nombres]

    def a(destino, nombre):
        mov.setdefault(destino, []).append(nombre)

    for p in sorted(R.iterdir()):
        if p.is_dir() or p.name.startswith("."):
            continue
        n, suf = p.name, p.suffix.lower()
        if n in DOCS_RAIZ or n in CONFIGS_VIVOS or n in protegidos:
            continue
        if n in CHATARRA:
            a("archivo_historico", n)
        elif n in BORRADORES:
            a("_salidas_locales/scratch_sesion", n)
        elif n in MANTENIMIENTO:
            a("mantenimiento", n)
        elif suf == ".md":
            a("docs", n)
        elif suf == ".json" and n.startswith("config"):
            a("configs_historicos", n)
        elif n.startswith("test_") and suf == ".py":
            if es_test_real(p):
                a("tests", n)
            elif n in tk:
                # esta en Git: se archiva, no se manda a una carpeta ignorada
                a("archivo_historico/pruebas_manuales", n)
            else:
                a("_salidas_locales/scratch_sesion", n)
        elif suf in (".log", ".err", ".out"):
            if n == "fase4_ejecucion.log":
                a("_salidas_locales/txt_reportes", n)
            else:
                a("BORRAR", n)
        elif suf == ".csv":
            a("_salidas_locales/csv", n)
        elif suf == ".txt":
            a("_salidas_locales/txt_reportes", n)
        elif suf == ".db":
            if n in ("catalogo.db", "photos_v2.db"):
                # photos_v2.db es la base VIVA (config_K.json la apunta aca) y
                # catalogo.db lo leen galeria.py y exportar_catalogo.py
                continue
            a("_salidas_locales", n)
        elif suf == ".py":
            a("archivo_historico", n)      # sin referencias y sin lanzador
        # lo demas (.service, .rules, .bat/.ps1/.sh vivos) se queda
    return mov, tk


def main():
    mov, tk = plan()
    print("=== PLAN (%s) ===" % ("EJECUTANDO" if HACER else "simulacro"))
    for destino in sorted(mov):
        xs = mov[destino]
        print("  %-34s %4d archivos  %s%s" % (
            destino, len(xs), ", ".join(xs[:3]), " ..." if len(xs) > 3 else ""))
    total = sum(len(v) for v in mov.values())
    print("  total a mover/borrar: %d" % total)
    if not HACER:
        return
    for destino, xs in mov.items():
        if destino == "BORRAR":
            for n in xs:
                (R / n).unlink()
            print("borrados %d archivos de ruido" % len(xs))
            continue
        (R / destino).mkdir(parents=True, exist_ok=True)
        for n in xs:
            dest = R / destino / n
            if n in tk:
                r = sh("git", "mv", n, str(dest.relative_to(R)).replace("\\", "/"))
                if r.returncode:
                    print("  git mv fallo en %s: %s" % (n, r.stderr.strip()[:80]))
            else:
                shutil.move(str(R / n), str(dest))
        print("movidos %d a %s" % (len(xs), destino))


if __name__ == "__main__":
    main()

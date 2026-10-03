r"""Corre Autocull sobre una lista de fotos y va escribiendo el avance a disco.

Lo lanza `web_curador.py` como subproceso **con el python del venv de Autocull**
(`C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe`), porque Autocull tiene sus
propias dependencias pesadas (onnxruntime, ultralytics, mediapipe) que no estan
en el entorno del curador. Por eso esto es un script suelto y no un import.

    <venv-autocull>\python.exe autocull_runner.py <lista.txt> <salida.json> [ajustes.json]

`lista.txt`: una ruta por linea (las fotos del evento, que viven desparramadas
en varias carpetas de K:, no en una sola).
`salida.json`: se reescribe con el avance mientras corre y con el resultado al
terminar. El curador lo lee tal cual para pintar la pagina.
"""
import json
import os
import sys
import time
import traceback

# El paquete autocull/ vive en este mismo proyecto desde el 30/9/2026. El venv sigue en
# C:\Renzo\Proyectos\Autocull\venv (mover un venv rompe sus rutas), pero el CODIGO que se
# importa es el de aca: va primero en sys.path, antes que cualquier copia instalada en el venv.
AUTOCULL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AUTOCULL)

# Los workers tienen que correr con 1 hilo por libreria o np.fft.fft2 se cuelga
# por thrashing (esta en el CLAUDE.md de Autocull: no sacar).
for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
          "OPENCV_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[v] = "1"


def escribir(salida, datos):
    """Escritura atomica: el curador lee este archivo mientras se actualiza."""
    tmp = salida + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False)
    os.replace(tmp, salida)


def main():
    lista, salida = sys.argv[1], sys.argv[2]
    with open(lista, encoding="utf-8") as fh:
        fotos = [l.strip() for l in fh if l.strip()]

    t0 = time.time()
    estado = {"estado": "arrancando", "hechas": 0, "total": len(fotos),
              "segundos": 0.0, "error": None, "fotos": []}
    # El curador deja escrito sobre que tipo de archivo mando a correr (RAW o
    # JPEG). Se conserva: un resultado guardado tiene que poder decir de donde
    # salio, porque las estrellas de una corrida sobre RAW y una sobre JPEG no
    # son la misma medicion.
    try:
        with open(salida, encoding="utf-8") as fh:
            estado["tipo"] = json.load(fh).get("tipo")
    except (OSError, json.JSONDecodeError):
        pass
    escribir(salida, estado)

    try:
        from autocull.config import AnalysisConfig
        from autocull.pipeline import run_pipeline

        cfg = AnalysisConfig()
        # 3er argumento opcional: JSON con ajustes de AnalysisConfig. seleccion_ig.py lo usa para un
        # Autocull que solo bota basura (sin cuotas fijas, 3 por rafaga, orden del ranker); el
        # curador no lo pasa y sigue con los valores de siempre.
        if len(sys.argv) > 3:
            with open(sys.argv[3], encoding="utf-8") as fh:
                for k, v in json.load(fh).items():
                    if not hasattr(cfg, k):
                        raise ValueError(f"ajuste desconocido de Autocull: {k}")
                    setattr(cfg, k, v)
        cfg.__post_init__()

        ultimo = [0.0]

        def avance(hechas, total):
            estado.update(estado="corriendo", hechas=hechas, total=total,
                          segundos=round(time.time() - t0, 1))
            # a disco como mucho 2 veces por segundo: son miles de fotos
            if time.time() - ultimo[0] > 0.5 or hechas == total:
                ultimo[0] = time.time()
                escribir(salida, estado)

        res = run_pipeline("", cfg, progress_callback=avance, files=fotos)

        estado["fotos"] = [{
            "ruta": r.get("filepath"),
            "puntaje": r.get("final_score"),
            "decision": r.get("decision"),
            "motivo": r.get("reject_reason") or r.get("error") or "",
        } for r in res]
        estado.update(estado="listo", segundos=round(time.time() - t0, 1))
        escribir(salida, estado)
    except Exception as exc:
        estado.update(estado="error", error=f"{exc}\n{traceback.format_exc()}",
                      segundos=round(time.time() - t0, 1))
        escribir(salida, estado)
        raise


if __name__ == "__main__":
    main()

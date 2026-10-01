"""
Utilidades de logging: log_step con timestamp y Spinner de consola.
Reconstruido a partir de los usos en 1_indexado.py, 2_analisis.py y 3_agrupacion_mejorado.py.
"""
import sys
import threading
import time
from datetime import datetime

# Consolas Windows suelen ser cp1252; evita UnicodeEncodeError con emojis/mojibake
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log_step(msg: str):
    """Imprime un paso con timestamp [HH:MM:SS]."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


class Spinner:
    """Spinner de consola en hilo daemon. El atributo .text puede mutarse en vivo."""

    _FRAMES = "|/-\\"

    def __init__(self, text: str = ""):
        self.text = text
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self):
        i = 0
        while not self._stop_event.is_set():
            frame = self._FRAMES[i % len(self._FRAMES)]
            # \r + padding para borrar restos de textos anteriores más largos
            sys.stdout.write(f"\r{frame} {self.text}" + " " * 10)
            sys.stdout.flush()
            i += 1
            time.sleep(0.1)

    def stop(self, final_msg: str = None):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        sys.stdout.write("\r" + " " * 100 + "\r")
        sys.stdout.flush()
        if final_msg:
            log_step(final_msg)

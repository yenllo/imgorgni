"""Launch the Autocull review app: ``python -m autocull.web``."""

import argparse
import sys
import webbrowser

import uvicorn


def main() -> None:
    # Windows consoles default to cp1252 and choke on non-ASCII output; make
    # stdout/stderr UTF-8 tolerant so the server never crashes on a print.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Autocull - local photo review web app")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"\n  Autocull Web  ->  {url}\n  (Ctrl+C para detener)\n")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # Import string keeps reload/spawn-safe semantics; the pipeline spawns
    # its own worker processes independently.
    uvicorn.run("autocull.web.server:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

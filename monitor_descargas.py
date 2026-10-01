import http.server
import socketserver
import glob
import os
import json

import string
DOWNLOAD_DIRS = [f"{d}:\\Descargas" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\Descargas")]

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Permitir peticiones desde el navegador (CORS)
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-type", "application/json")
        self.end_headers()
        
        active_count = 0
        downloaded_zips = []
        import string
        download_dirs = [f"{d}:\\Descargas" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\Descargas")]
        for d in download_dirs:
            try:
                if os.path.exists(d):
                    active_count += len(glob.glob(os.path.join(d, "*.crdownload")))
                    active_count += len(glob.glob(os.path.join(d, "*.tmp")))
                    zips = glob.glob(os.path.join(d, "*.zip"))
                    downloaded_zips.extend([os.path.basename(z) for z in zips])
            except Exception as e:
                pass # Ignorar errores si el disco se desconecta en medio del escaneo
        
        response = {
            "can_download": (active_count < 4),
            "active_count": active_count,
            "downloaded_zips": downloaded_zips
        }
        
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def log_message(self, format, *args):
        pass # Silenciar logs

PORT = 8765
print(f"Monitor escuchando en http://127.0.0.1:{PORT}")
print(f"Vigilando las carpetas: {DOWNLOAD_DIRS}")
print("El navegador usara esto para saber cuando termino la descarga anterior.")

socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
    httpd.serve_forever()

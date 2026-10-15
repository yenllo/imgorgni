import os
import urllib.request
import urllib.parse
import time
import sys

# Configuracion
DESTINO = r"F:\Descargas"
ENLACES_TXT = "enlaces.txt"

def descargar_con_progreso(url, ruta_destino):
    """Descarga un archivo mostrando una barra de progreso."""
    print(f"\nDescargando: {os.path.basename(ruta_destino)}")
    
    # Si hay un .crdownload de una descarga fallida, lo borramos para empezar limpio
    ruta_temp = ruta_destino + ".crdownload"
    if os.path.exists(ruta_temp):
        try:
            os.remove(ruta_temp)
        except:
            pass

    try:
        def reporte(bloques_leidos, tamano_bloque, tamano_total):
            if tamano_total > 0:
                descargado = bloques_leidos * tamano_bloque
                porcentaje = (descargado / tamano_total) * 100
                mb_descargados = descargado / (1024 * 1024)
                mb_total = tamano_total / (1024 * 1024)
                sys.stdout.write(f"\r  Progreso: [{porcentaje:5.1f}%] {mb_descargados:8.1f} MB / {mb_total:.1f} MB")
                sys.stdout.flush()

        urllib.request.urlretrieve(url, ruta_temp, reporthook=reporte)
        sys.stdout.write("\n")
        
        # Renombrar al nombre final cuando termina completo
        if os.path.exists(ruta_destino):
            os.remove(ruta_destino)
        os.rename(ruta_temp, ruta_destino)
        print(f"  OK -> Finalizado.")
        
    except Exception as e:
        print(f"\n  ERROR al descargar: {e}")
        if os.path.exists(ruta_temp):
            try:
                os.remove(ruta_temp)
            except:
                pass

def main():
    if not os.path.exists(ENLACES_TXT):
        print(f"ERROR: No se encontro el archivo '{ENLACES_TXT}'.")
        print("Crea un archivo llamado enlaces.txt en esta misma carpeta, pega todos tus enlaces de Lightroom ahi y vuelve a correr el script.")
        return

    with open(ENLACES_TXT, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip() and line.startswith('http')]

    if not urls:
        print("No se encontraron URLs validas en enlaces.txt.")
        return

    print(f"Se encontraron {len(urls)} enlaces en enlaces.txt.")
    
    os.makedirs(DESTINO, exist_ok=True)
    
    faltantes = 0
    for url in urls:
        # Extraer el nombre del archivo de la URL (ej: LightroomLibrary_24.zip)
        parsed = urllib.parse.urlparse(url)
        nombre = os.path.basename(parsed.path)
        if not nombre.endswith('.zip'):
            # Fallback por si la URL tiene parametros raros
            # Buscamos algo parecido a LightroomLibrary_xx.zip
            if "LightroomLibrary_" in url:
                idx = url.find("LightroomLibrary_")
                fin = url.find(".zip", idx) + 4
                if fin > 3:
                    nombre = url[idx:fin]
                else:
                    continue
            else:
                continue
                
        ruta_final = os.path.join(DESTINO, nombre)
        
        # Chequear si ya esta descargado y parece completo (> 100 MB al menos)
        if os.path.exists(ruta_final) and os.path.getsize(ruta_final) > 100 * 1024 * 1024:
            continue
            
        faltantes += 1
        descargar_con_progreso(url, ruta_final)
        
        # Pausa de 3 segundos entre descargas para no saturar
        time.sleep(3)

    if faltantes == 0:
        print("\n¡Todo listo! No faltaba ningun archivo. Todos los enlaces ya estan descargados en F:\\Descargas.")
    else:
        print(f"\n¡Proceso finalizado! Se descargaron {faltantes} archivos faltantes.")

if __name__ == "__main__":
    main()

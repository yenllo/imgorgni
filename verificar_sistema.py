"""
Script de VerificaciÃ³n y ConfiguraciÃ³n Inicial
Para el Organizador de Fotos con IA
"""

import os
import json
import sys

from db import DiscoCaido, verificar_disco_montado
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def check_file_exists(path, name):
    """Verifica si un archivo existe"""
    if os.path.exists(path):
        print(f"âœ… {name}: {path}")
        return True
    else:
        print(f"âŒ {name} NO encontrado: {path}")
        return False

def check_directory_exists(path, name):
    """Verifica si un directorio existe"""
    if os.path.exists(path) and os.path.isdir(path):
        # Contar archivos de imagen
        image_exts = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.raw', '.cr2', '.arw', '.nef'}
        image_count = 0
        
        for root, dirs, files in os.walk(path):
            for file in files:
                if Path(file).suffix.lower() in image_exts:
                    image_count += 1
                    if image_count >= 100:  # Detenerse despuÃ©s de contar 100
                        break
            if image_count >= 100:
                break
        
        print(f"âœ… {name}: {path}")
        if image_count > 0:
            print(f"   â””â”€â”€ Se encontraron {image_count}+ archivos de imagen")
        else:
            print(f"   âš ï¸  No se encontraron imÃ¡genes en esta carpeta")
        return True
    else:
        print(f"âŒ {name} NO encontrado: {path}")
        return False

def check_python_module(module_name):
    """Verifica si un mÃ³dulo de Python estÃ¡ instalado"""
    try:
        __import__(module_name)
        print(f"âœ… MÃ³dulo Python: {module_name}")
        return True
    except ImportError:
        print(f"âŒ MÃ³dulo Python NO instalado: {module_name}")
        return False

def verify_config(config_path):
    """Verifica que el archivo de configuraciÃ³n sea vÃ¡lido"""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        print("\nðŸ“‹ CONFIGURACIÃ“N:")
        print(f"   Carpeta de fotos: {config.get('root_dir', 'NO DEFINIDO')}")
        print(f"   Base de datos: {config.get('db_path', 'NO DEFINIDO')}")
        print(f"   Marca de agua: {config.get('watermark_path', 'NO DEFINIDO')}")
        print(f"   Modelo YOLO: {config.get('yolo_onnx_path', 'NO DEFINIDO')}")
        print(f"   Modelo Features: {config.get('feature_model_onnx_path', 'NO DEFINIDO')}")
        
        # Verificar rutas crÃ­ticas
        critical_ok = True
        if not check_directory_exists(config.get('root_dir', ''), "Carpeta de fotos"):
            critical_ok = False
        
        return config, critical_ok
    except Exception as e:
        print(f"âŒ Error al leer config.json: {e}")
        return None, False

def main():
    print("=" * 60)
    print("   VERIFICACIÃ“N DEL SISTEMA - ORGANIZADOR DE FOTOS IA")
    print("=" * 60)
    print()
    
    # Rutas base: el proyecto es esta carpeta; las fotos salen de config.json
    project_dir = Path(__file__).parent.resolve()
    # config_K.json primero: config.json apunta a una carpeta que ya no existe
    # (C:\...\IMGorgniotos) desde que todo vive en K:
    photos_dir = project_dir / "fotos"
    for nombre in ("config_K.json", "config.json"):
        try:
            with open(project_dir / nombre, "r", encoding="utf-8") as f:
                candidato = Path(json.load(f)["root_dir"])
            if candidato.exists():
                photos_dir = candidato
                break
        except Exception:
            continue

    # Si el NAS no esta montado, TODO va a dar "falta": se dice una vez y se
    # corta, en vez de reportar 136.260 fotos ausentes por un cable flojo.
    try:
        verificar_disco_montado(str(photos_dir))
    except DiscoCaido as e:
        print("   NAS CAIDO: %s" % e)
        print("   Revisa el cable o el montaje y volve a correr esto.")
        sys.exit(2)
    
    print("ðŸ“‚ VERIFICACIÃ“N DE DIRECTORIOS:")
    print("-" * 40)
    
    # Verificar directorio del proyecto
    project_ok = check_directory_exists(project_dir, "Directorio del proyecto")
    
    # Verificar directorio de fotos
    photos_ok = check_directory_exists(photos_dir, "Directorio de fotos")
    
    print()
    print("ðŸ“„ VERIFICACIÃ“N DE ARCHIVOS PRINCIPALES:")
    print("-" * 40)
    
    # Cambiar al directorio del proyecto si existe
    if project_ok:
        os.chdir(project_dir)
    
    # Archivos principales
    files_to_check = [
        ("main.py", "Archivo principal"),
        ("1_indexado.py", "Fase 1 - IndexaciÃ³n"),
        ("2_analisis.py", "Fase 2 - AnÃ¡lisis IA"),
        ("3_agrupacion_mejorado.py", "Fase 3 - Clustering"),
        ("app.py", "Interfaz web"),
        ("db.py", "GestiÃ³n de base de datos"),
        ("utils_images.py", "Utilidades de imagen"),
        ("utils_log.py", "Utilidades de logging"),
        ("config.json", "ConfiguraciÃ³n"),
        ("requirements.txt", "Dependencias"),
        ("Marca_rb_fotos_.png", "Marca de agua"),
    ]
    
    all_files_ok = True
    for filename, description in files_to_check:
        if not check_file_exists(filename, description):
            all_files_ok = False
    
    print()
    print("ðŸ VERIFICACIÃ“N DE PYTHON Y MÃ“DULOS:")
    print("-" * 40)
    
    # Verificar versiÃ³n de Python
    python_version = sys.version_info
    if python_version >= (3, 10):
        print(f"âœ… Python {python_version.major}.{python_version.minor}.{python_version.micro}")
    else:
        print(f"âŒ Python {python_version.major}.{python_version.minor} (se requiere 3.10+)")
    
    # MÃ³dulos crÃ­ticos
    modules = [
        "cv2",          # opencv-python
        "numpy",
        "PIL",          # Pillow
        "rawpy",
        "exifread",
        "onnxruntime",
        "sklearn",
        "streamlit",
        "tqdm"
    ]
    
    all_modules_ok = True
    for module in modules:
        if not check_python_module(module):
            all_modules_ok = False
    
    # Verificar configuraciÃ³n
    config = None
    config_ok = False
    if all_files_ok and os.path.exists("config.json"):
        print()
        config, config_ok = verify_config("config.json")
    
    # Crear carpeta de modelos si no existe
    models_dir = project_dir / "models"
    if not models_dir.exists():
        print()
        print("ðŸ“ Creando carpeta de modelos...")
        models_dir.mkdir(parents=True, exist_ok=True)
        print(f"âœ… Carpeta creada: {models_dir}")
    
    # Resumen final
    print()
    print("=" * 60)
    print("   RESUMEN DE VERIFICACIÃ“N")
    print("=" * 60)
    
    issues = []
    
    if not project_ok:
        issues.append("âŒ El directorio del proyecto no existe")
    if not photos_ok:
        issues.append("âŒ El directorio de fotos no existe o estÃ¡ vacÃ­o")
    if not all_files_ok:
        issues.append("âŒ Faltan algunos archivos del proyecto")
    if not all_modules_ok:
        issues.append("âŒ Faltan algunos mÃ³dulos de Python")
    if not config_ok:
        issues.append("âŒ Hay problemas con la configuraciÃ³n")
    
    if issues:
        print("âš ï¸  SE ENCONTRARON PROBLEMAS:")
        print()
        for issue in issues:
            print(f"  {issue}")
        print()
        print("SOLUCIONES SUGERIDAS:")
        print("-" * 40)
        
        if not project_ok:
            print("1. Copia todo el cÃ³digo a C:\\Git\\Organizador_Fotos")
        
        if not photos_ok:
            print("2. Verifica que las fotos estÃ©n en C:\\Git\\CarpetasExternas\\fotos")
        
        if not all_modules_ok:
            print("3. Instala las dependencias faltantes:")
            print("   pip install -r requirements.txt")
            print("   O individualmente:")
            print("   pip install opencv-python numpy Pillow rawpy exifread")
            print("   pip install onnxruntime-directml streamlit scikit-learn")
        
        if not config_ok:
            print("4. Copia el archivo config.json actualizado al directorio del proyecto")
        
        print()
        print("5. Descarga los modelos ONNX ejecutando:")
        print("   python setup_models.py")
    else:
        print("âœ… Â¡SISTEMA LISTO PARA EJECUTAR!")
        print()
        print("Puedes iniciar el proceso con:")
        print("  1. Ejecutar el archivo: ejecutar_actualizado.bat")
        print("  2. O manualmente:")
        print("     python main.py -ph 1  # Fase 1: IndexaciÃ³n")
        print("     python main.py -ph 2  # Fase 2: AnÃ¡lisis IA")
        print("     python main.py -ph 3  # Fase 3: Clustering")
        print("     streamlit run app.py  # Interfaz web")
    
    print()
    print("=" * 60)

if __name__ == "__main__":
    main()
    input("\nPresiona Enter para continuar...")

"""
Script de diagnÃ³stico para verificar instalaciÃ³n
"""

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

print("=" * 60)
print("ðŸ” DIAGNÃ“STICO DEL SISTEMA")
print("=" * 60)
print()

# 1. Verificar Python
print(f"âœ… Python {sys.version}")
print(f"   Ejecutable: {sys.executable}")
print()

# 2. Verificar directorio actual
print(f"ðŸ“ Directorio actual: {Path.cwd()}")
print(f"ðŸ“ Directorio del script: {Path(__file__).parent.absolute()}")
print()

# 3. Verificar archivos principales
archivos_requeridos = [
    "main.py",
    "1_indexado.py",
    "2_analisis.py",
    "3_agrupacion_mejorado.py",
    "app.py",
    "db.py",
    "utils_images.py",
    "utils_log.py",
    "config.json",
]

print("ðŸ“‹ Archivos principales:")
for archivo in archivos_requeridos:
    ruta = Path(__file__).parent / archivo
    if ruta.exists():
        size_kb = ruta.stat().st_size / 1024
        print(f"   âœ… {archivo:<20} ({size_kb:.1f} KB)")
    else:
        print(f"   âŒ {archivo:<20} NO ENCONTRADO")
print()

# 4. Verificar config.json
config_path = Path(__file__).parent / "config.json"
if config_path.exists():
    import json
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        
        print("âš™ï¸  ConfiguraciÃ³n (config.json):")
        print(f"   root_dir: {cfg.get('root_dir', 'NO DEFINIDO')}")
        print(f"   db_path: {cfg.get('db_path', 'NO DEFINIDO')}")
        print(f"   yolo_onnx_path: {cfg.get('yolo_onnx_path', 'NO DEFINIDO')}")
        print(f"   feature_model_onnx_path: {cfg.get('feature_model_onnx_path', 'NO DEFINIDO')}")
        
        # Verificar que root_dir existe
        root_dir = Path(cfg.get('root_dir', ''))
        if root_dir.exists():
            print(f"   âœ… root_dir existe")
        else:
            print(f"   âš ï¸  root_dir NO existe: {root_dir}")
        
    except json.JSONDecodeError as e:
        print(f"   âŒ Error al leer config.json: {e}")
    except Exception as e:
        print(f"   âŒ Error: {e}")
else:
    print("âŒ config.json NO ENCONTRADO")
print()

# 5. Verificar dependencias
print("ðŸ“¦ Dependencias Python:")
dependencias = [
    "cv2",
    "numpy",
    "PIL",
    "rawpy",
    "onnxruntime",
    "sklearn",
    "streamlit",
    "tqdm",
]

for dep in dependencias:
    try:
        __import__(dep)
        print(f"   âœ… {dep}")
    except ImportError:
        print(f"   âŒ {dep} NO INSTALADO")
print()

# 6. Verificar ONNX Runtime y DML
print("ðŸš€ ONNX Runtime:")
try:
    import onnxruntime as ort
    print(f"   âœ… onnxruntime {ort.__version__}")
    providers = ort.get_available_providers()
    print(f"   Providers disponibles:")
    for p in providers:
        icon = "âœ…" if "Dml" in p else "â„¹ï¸"
        print(f"      {icon} {p}")
except ImportError:
    print("   âŒ onnxruntime NO INSTALADO")
except Exception as e:
    print(f"   âš ï¸  Error: {e}")
print()

# 7. Verificar modelos ONNX
print("ðŸ¤– Modelos ONNX:")
if config_path.exists():
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        
        yolo_path = Path(cfg.get('yolo_onnx_path', ''))
        feat_path = Path(cfg.get('feature_model_onnx_path', ''))
        
        if yolo_path.exists():
            size_mb = yolo_path.stat().st_size / (1024 * 1024)
            print(f"   âœ… YOLO: {yolo_path.name} ({size_mb:.1f} MB)")
        else:
            print(f"   âŒ YOLO NO encontrado: {yolo_path}")
        
        if feat_path.exists():
            size_mb = feat_path.stat().st_size / (1024 * 1024)
            print(f"   âœ… Features: {feat_path.name} ({size_mb:.1f} MB)")
        else:
            print(f"   âŒ Features NO encontrado: {feat_path}")
            
    except Exception as e:
        print(f"   âš ï¸  Error verificando modelos: {e}")
print()

# 8. Verificar base de datos
print("ðŸ’¾ Base de datos:")
if config_path.exists():
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        
        db_path = Path(cfg.get('db_path', ''))
        if db_path.exists():
            size_mb = db_path.stat().st_size / (1024 * 1024)
            print(f"   âœ… {db_path.name} ({size_mb:.1f} MB)")
            
            # Intentar conectar
            import sqlite3
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM files")
                count = cursor.fetchone()[0]
                print(f"   âœ… Fotos indexadas: {count:,}")
                conn.close()
            except Exception as e:
                print(f"   âš ï¸  Error al leer BD: {e}")
        else:
            print(f"   â„¹ï¸  BD no existe aÃºn (se crearÃ¡ en Fase 1): {db_path}")
            
    except Exception as e:
        print(f"   âš ï¸  Error: {e}")
print()

# 9. Recomendaciones
print("=" * 60)
print("ðŸ’¡ RECOMENDACIONES:")
print("=" * 60)

# Verificar si hay problemas crÃ­ticos
problemas = []

if not (Path(__file__).parent / "main.py").exists():
    problemas.append("âŒ main.py no encontrado - Reinstala el proyecto")

if not config_path.exists():
    problemas.append("âŒ config.json no encontrado - Copia config.json.example")

try:
    __import__("onnxruntime")
except ImportError:
    problemas.append("âŒ onnxruntime no instalado - Ejecuta: pip install onnxruntime-directml")

if problemas:
    print("âš ï¸  PROBLEMAS ENCONTRADOS:")
    for p in problemas:
        print(f"   {p}")
    print()
    print("   Para instalar dependencias:")
    print("   pip install -r requirements.txt")
else:
    print("âœ… Sistema configurado correctamente")
    print()
    print("   Para ejecutar:")
    print("   python main.py -ph 1    # Fase 1: IndexaciÃ³n")
    print("   python main.py -ph 2    # Fase 2: AnÃ¡lisis IA")
    print("   python main.py -ph 3    # Fase 3: Clustering")

print()
print("=" * 60)

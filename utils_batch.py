"""
Utilidades para procesamiento en batch optimizado
Usa mÃ¡s RAM para reducir tiempo de procesamiento
"""

import numpy as np
from pathlib import Path
from typing import List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils_images import load_image_array


def load_images_batch(file_paths: List[str], max_workers: int = 4) -> List[Tuple[int, Optional[np.ndarray]]]:
    """
    Carga mÃºltiples imÃ¡genes en paralelo usando threads
    
    Args:
        file_paths: Lista de rutas a cargar
        max_workers: NÃºmero de threads paralelos
    
    Returns:
        Lista de tuplas (Ã­ndice, imagen_array), None si falla
    """
    results = [None] * len(file_paths)
    
    def load_single(idx: int, path: str) -> Tuple[int, Optional[np.ndarray]]:
        try:
            img = load_image_array(path)
            return (idx, img)
        except Exception:
            return (idx, None)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(load_single, i, fp): i 
            for i, fp in enumerate(file_paths)
        }
        
        for future in as_completed(futures):
            idx, img = future.result()
            results[idx] = (idx, img)
    
    return results


def preload_images_to_ram(file_paths: List[str], max_ram_mb: int = 2048) -> dict:
    """
    Pre-carga imÃ¡genes en RAM hasta un lÃ­mite de memoria
    
    Args:
        file_paths: Rutas de imÃ¡genes a cargar
        max_ram_mb: LÃ­mite de RAM a usar (MB)
    
    Returns:
        Dict con {file_path: np.ndarray}
    """
    cache = {}
    ram_used_mb = 0
    max_ram_bytes = max_ram_mb * 1024 * 1024
    
    for fp in file_paths:
        if ram_used_mb >= max_ram_bytes:
            break
        
        try:
            img = load_image_array(fp)
            img_size_mb = img.nbytes / (1024 * 1024)
            
            if ram_used_mb + img_size_mb <= max_ram_bytes:
                cache[fp] = img
                ram_used_mb += img_size_mb
        except Exception:
            continue
    
    return cache


def chunk_list(items: List, chunk_size: int) -> List[List]:
    """
    Divide lista en chunks de tamaÃ±o fijo
    
    Args:
        items: Lista a dividir
        chunk_size: TamaÃ±o de cada chunk
    
    Returns:
        Lista de chunks
    """
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]

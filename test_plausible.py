"""python test_plausible.py

_plausible() rechazaba fotos JPEG sanas: el umbral de 0.02 bytes/pixel era un
numero tirado sin medir. Sobre 3.892 archivos marcados "no medibles", 2.800
abrian y decodificaban perfecto -- hasta 0.0021 bytes/pixel. El caso real que
el filtro tiene que frenar (DNG del rescate de F: declarando 49435x40988 en
23 MB) da ~0.0000119. 0.0005 separa los dos con margen de sobra.
"""
import sys

sys.path.insert(0, r"C:\Renzo\Proyectos\IMGorgni")
import importlib.util

spec = importlib.util.spec_from_file_location("wc", r"C:\Renzo\Proyectos\IMGorgni\web_curador.py")
wc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wc)


def test_acepta_el_jpeg_mas_comprimido_medido():
    """El bpp mas bajo real medido en la biblioteca (0.0021). Antes lo rechazaba."""
    assert wc._plausible(6000, 4000, 373_931) is True
    print("OK: acepta 24MP en 374KB (0.0156 bpp) -- el caso que antes se perdia")


def test_rechaza_el_dng_dañado_del_rescate():
    """El caso real del docstring: 2 Gpx en 23 MB."""
    assert wc._plausible(49435, 40988, 23_000_000) is False
    print("OK: sigue rechazando el DNG dañado (0.0000119 bpp)")


def test_tope_de_megapixeles_sigue_activo():
    """Independiente del bpp: nada pasa de MAX_MPX."""
    assert wc._plausible(50000, 40000, 500_000_000) is False
    print("OK: MAX_MPX sigue frenando resoluciones absurdas aunque el bpp de bien")


def test_foto_real_de_200mp_pasa():
    """Panoramica de celular real, ya vista en la biblioteca."""
    assert wc._plausible(16320, 12240, 15_844_996) is True
    print("OK: acepta una panoramica de 200MP real (0.079 bpp)")


def test_dimensiones_invalidas_se_rechazan():
    assert wc._plausible(0, 100, 1000) is False
    assert wc._plausible(100, -5, 1000) is False
    assert wc._plausible(None, 100, 1000) is False
    print("OK: dimensiones con ceros/negativos/None se rechazan igual")


if __name__ == "__main__":
    test_acepta_el_jpeg_mas_comprimido_medido()
    test_rechaza_el_dng_dañado_del_rescate()
    test_tope_de_megapixeles_sigue_activo()
    test_foto_real_de_200mp_pasa()
    test_dimensiones_invalidas_se_rechazan()

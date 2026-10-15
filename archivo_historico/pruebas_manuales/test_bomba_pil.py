"""El tope anti-"bomba de descompresion" de PIL no puede tapar fotos reales.

PIL rechaza por defecto cualquier imagen de mas de 178,9 Mpx. Es una defensa
para servidores que reciben archivos de desconocidos; aca las fotos son propias
y el modo alta resolucion del telefono da 16320x12240 = 199,8 Mpx. Con el tope
por defecto, 114 fotos sanas salian "no se pudo leer" en el curador y
reclasificar_danados.py estaba a un paso de marcarlas danado=1.

    python test_bomba_pil.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image

import utils_images  # noqa: F401  -- al importarse sube el tope de PIL

TELEFONO = (16320, 12240)   # 199,8 Mpx, el caso real que fallaba


def main():
    assert Image.MAX_IMAGE_PIXELS is None or Image.MAX_IMAGE_PIXELS >= 300 * 10 ** 6, \
        f"el tope quedo en {Image.MAX_IMAGE_PIXELS}, no alcanza para las fotos del archivo"

    # una imagen del tamaño real del telefono tiene que abrirse sin protestar
    ancho, alto = TELEFONO
    assert ancho * alto > 178_956_970, "el caso de prueba ya no supera el tope viejo de PIL"

    # se arma chica y se le miente el tamaño en el encabezado: generar 199 Mpx de
    # verdad son ~600 MB de RAM y no hace falta para probar el limite.
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buf, "JPEG")
    buf.seek(0)
    im = Image.open(buf)
    im._size = TELEFONO
    from PIL import Image as _I
    _I._decompression_bomb_check(im.size)   # con el tope viejo, esto tiraba

    print(f"OK: PIL acepta hasta {Image.MAX_IMAGE_PIXELS/1e6:,.0f} Mpx "
          f"({ancho}x{alto} = {ancho*alto/1e6:.1f} Mpx entra sin problema)")


if __name__ == "__main__":
    main()

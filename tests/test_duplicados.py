"""
Chequeos del detector de duplicados. Esta lógica decide qué archivos se descartan,
así que se verifica el agrupamiento Y el criterio de cuál copia se conserva.

    python test_duplicados.py
"""
import sys

sys.path.insert(0, ".")
import importlib

dup = importlib.import_module("5_duplicados")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def cand(path, size=1000, cam="Canon", ph="0000000000000000", dims=(1000, 1000),
         danado=0, existe=True):
    c = dup.Candidato(1, path, size, "2023-01-01T00:00:00", cam, ph,
                      danado=danado, existe=existe)
    c._dims = dims  # evitar tocar el disco en los tests
    return c


def test_danada_pierde_aunque_sea_raw():
    """El error de agosto 2026: 594 pares conservaban la copia ROTA porque era
    la de mayor resolucion / la rescatada, y descartaban la sana."""
    rota = cand("/rescatadas/IMG_1.CR2", size=25_000_000, dims=(6000, 4000), danado=1)
    sana = cand("/Fotos_Organizadas/IMG_1.jpg", size=3_000_000, dims=(4000, 3000))
    assert max([rota, sana], key=lambda c: c.puntaje()) is sana, \
        "una copia danada no se conserva ni siendo RAW ni teniendo mas pixeles"
    print("OK: la copia danada nunca se conserva")


def test_entre_dos_rotas_gana_la_menos_rota():
    """danado ya no es si/no: 1 perdio imagen, 2 solo perdio el marcador de fin.

    Contar los dos como 'roto' inflo el daño 10x (13.733 vs ~1.400 reales) y,
    peor, al elegir entre dos copias rotas dejaba pasar cualquiera.
    """
    perdio = cand("/rescatadas/IMG_1.jpg", dims=(6000, 4000), danado=1)
    marcador = cand("/rescatadas/IMG_1 (2).jpg", dims=(4000, 3000), danado=2)
    assert max([perdio, marcador], key=lambda c: c.puntaje()) is marcador, \
        "la que solo perdio el marcador conserva la imagen entera"

    # y una sana le gana a las dos, aunque tenga menos pixeles
    sana = cand("/Fotos_Organizadas/IMG_1.jpg", dims=(1000, 800))
    assert max([perdio, marcador, sana], key=lambda c: c.puntaje()) is sana
    print("OK: entre dos copias rotas gana la que solo perdio el marcador de fin")


def test_la_que_no_esta_en_disco_pierde():
    fantasma = cand("/rescatadas/IMG_1.CR2", size=0, dims=(6000, 4000), existe=False)
    real = cand("/Fotos_Organizadas/IMG_1.jpg", dims=(4000, 3000))
    assert max([fantasma, real], key=lambda c: c.puntaje()) is real
    print("OK: una ruta muerta no se elige como copia a conservar")


def test_organizada_gana_el_desempate():
    """Entre dos JPEG iguales ganaba la ruta mas corta, que casi siempre era la
    rescatada — justo la carpeta con 22,7% de danio."""
    org = cand("/K/Fotos_Organizadas/Viajes/Italia 2025/DSC01.jpg", dims=(4000, 3000))
    resc = cand("/K/Fotos_F_rescatadas/DSC01.jpg", dims=(4000, 3000))
    assert max([resc, org], key=lambda c: c.puntaje()) is org
    print("OK: entre copias equivalentes gana la del arbol organizado")


def test_cuarentena_se_reconoce():
    assert dup.en_cuarentena(r"K:\_duplicados_cuarentena\E\foto.jpg")
    assert not dup.en_cuarentena(r"K:\Fotos_Organizadas\foto.jpg")
    print("OK: reconoce lo que ya esta en cuarentena")


def test_raw_gana_a_jpg():
    raw = cand("/fotos/IMG_1234.CR2", size=25_000_000, dims=(6000, 4000))
    jpg = cand("/fotos/IMG_1234.JPG", size=3_000_000, dims=(6000, 4000))
    mejor = max([jpg, raw], key=lambda c: c.puntaje())
    assert mejor is raw, "El RAW debe ganarle al JPG"
    print("OK: RAW se conserva sobre JPG")


def test_resolucion_gana():
    grande = cand("/fotos/a.jpg", size=3_000_000, dims=(6000, 4000))
    chica = cand("/fotos/b.jpg", size=200_000, dims=(1024, 768))
    assert max([chica, grande], key=lambda c: c.puntaje()) is grande
    print("OK: mayor resolución se conserva")


def test_exif_gana():
    con = cand("/fotos/a.jpg", cam="Canon EOS R", dims=(4000, 3000))
    sin = cand("/fotos/b.jpg", cam=None, dims=(4000, 3000))
    assert max([sin, con], key=lambda c: c.puntaje()) is con
    print("OK: la copia con EXIF de cámara se conserva")


def test_whatsapp_pierde():
    orig = cand("/fotos/2023/IMG_1234.jpg", dims=(4000, 3000))
    wa = cand("/WhatsApp/Media/IMG-20230101-WA0001.jpg", dims=(4000, 3000))
    assert max([wa, orig], key=lambda c: c.puntaje()) is orig
    assert dup.penalizacion_ruta(wa.path) > 0
    print("OK: la copia de WhatsApp se descarta")


def test_agrupa_identicos():
    h = "ffffffffffffffff"
    grupo = [cand(f"/f/{i}.jpg", ph=h) for i in range(3)]
    grupos = dup.agrupar_por_phash(grupo, umbral=5)
    assert len(grupos) == 1 and len(grupos[0]) == 3, grupos
    print("OK: agrupa 3 copias con phash idéntico")


def test_agrupa_cercanos_no_lejanos():
    # difieren en 2 bits del último byte
    a = cand("/f/a.jpg", ph="ff00ff00ff00ff00")
    b = cand("/f/b.jpg", ph="ff00ff00ff00ff03")
    lejano = cand("/f/c.jpg", ph="00ff00ff00ff00ff")  # 64 bits de diferencia
    grupos = dup.agrupar_por_phash([a, b, lejano], umbral=5)
    assert len(grupos) == 1, f"Esperaba 1 grupo, hay {len(grupos)}"
    paths = {c.path for c in grupos[0]}
    assert paths == {"/f/a.jpg", "/f/b.jpg"}, paths
    print("OK: agrupa cercanos (2 bits) y excluye lejanos (64 bits)")


def test_umbral_respetado():
    a = cand("/f/a.jpg", ph="0000000000000000")
    b = cand("/f/b.jpg", ph="000000000000000f")  # 4 bits de diferencia
    assert len(dup.agrupar_por_phash([a, b], umbral=5)) == 1, "umbral 5 debe agrupar 4 bits"
    assert len(dup.agrupar_por_phash([a, b], umbral=3)) == 0, "umbral 3 NO debe agrupar 4 bits"
    print("OK: el umbral se respeta (agrupa a 5, no a 3)")


def test_transitividad():
    # A~B (1 bit), B~C (1 bit), A~C (2 bits) -> los tres en un grupo
    a = cand("/f/a.jpg", ph="0000000000000000")
    b = cand("/f/b.jpg", ph="0000000000000001")
    c = cand("/f/c.jpg", ph="0000000000000003")
    grupos = dup.agrupar_por_phash([a, b, c], umbral=2)
    assert len(grupos) == 1 and len(grupos[0]) == 3, grupos
    print("OK: fusiona grupos transitivos (A~B~C)")


def test_raw_jpg_por_nombre():
    grupos = dup.agrupar_raw_jpg([
        cand("/f/IMG_1234.CR2", ph="aaaa000000000000"),
        cand("/f/IMG_1234.JPG", ph="bbbb000000000000"),
        cand("/f/OTRA.JPG", ph="cccc000000000000"),
    ])
    assert len(grupos) == 1 and len(grupos[0]) == 2, grupos
    print("OK: empareja RAW+JPG por nombre base")


def test_no_agrupa_solo_jpgs_mismo_nombre():
    # dos JPG con el mismo nombre pero sin RAW: no es par RAW+JPG
    grupos = dup.agrupar_raw_jpg([
        cand("/a/IMG_1.JPG"), cand("/b/IMG_1.JPG"),
    ])
    assert grupos == [], "sin RAW no debe formar par RAW+JPG"
    print("OK: no confunde JPG homónimos con pares RAW+JPG")


def test_clasificacion():
    h = "ff00ff00ff00ff00"
    # mismo numero de secuencia + phash identico: la misma foto reexportada
    mismo = [cand("/f/DSC01234.jpg", ph=h),
             cand("/backup/DSC01234_editado.jpg", ph=h)]
    assert dup.clasificar(mismo) == "RECOMPRIMIDO"
    # rafaga de deporte: frames distintos, phash identico pero numeros distintos
    rafaga = [cand("/deporte/DSC01234.jpg", ph=h),
              cand("/deporte/DSC01235.jpg", ph=h)]
    assert dup.clasificar(rafaga) == "SIMILAR", "sin mismo numero no es recompresion"
    # sin numero de secuencia en el nombre: no hay con que desempatar
    sin_numero = [cand("/f/foto.jpg", ph=h), cand("/f/captura.jpg", ph=h)]
    assert dup.clasificar(sin_numero) == "SIMILAR"
    # phash lejano: ni con el mismo numero pasa de SIMILAR
    lejos = [cand("/f/DSC01234.jpg", ph="0000000000000000"),
             cand("/f/DSC01234 (2).jpg", ph="000000000000003f")]  # 6 bits
    assert dup.clasificar(lejos) == "SIMILAR"
    print("OK: clasifica RECOMPRIMIDO (mismo nro) vs SIMILAR (rafaga/sin nro/lejano)")


def test_escala():
    """El multi-index hashing debe manejar 20k fotos sin explotar."""
    import random, time
    random.seed(0)
    muchos = [cand(f"/f/{i}.jpg", ph=f"{random.getrandbits(64):016x}")
              for i in range(20000)]
    # 200 duplicados reales inyectados
    for i in range(0, 400, 2):
        muchos[i + 1] = cand(f"/f/dup{i}.jpg", ph=muchos[i].phash)
    t0 = time.time()
    grupos = dup.agrupar_por_phash(muchos, umbral=5)
    elapsed = time.time() - t0
    assert elapsed < 30, f"Demasiado lento: {elapsed:.1f}s para 20k"
    assert len(grupos) >= 190, f"Esperaba ~200 grupos, encontró {len(grupos)}"
    print(f"OK: 20.000 fotos en {elapsed:.1f}s -> {len(grupos)} grupos")


if __name__ == "__main__":
    test_danada_pierde_aunque_sea_raw()
    test_entre_dos_rotas_gana_la_menos_rota()
    test_la_que_no_esta_en_disco_pierde()
    test_organizada_gana_el_desempate()
    test_cuarentena_se_reconoce()
    test_raw_gana_a_jpg()
    test_resolucion_gana()
    test_exif_gana()
    test_whatsapp_pierde()
    test_agrupa_identicos()
    test_agrupa_cercanos_no_lejanos()
    test_umbral_respetado()
    test_transitividad()
    test_raw_jpg_por_nombre()
    test_no_agrupa_solo_jpgs_mismo_nombre()
    test_clasificacion()
    test_escala()
    print("\n✅ TODOS LOS CHEQUEOS DEL DETECTOR DE DUPLICADOS PASARON")

"""python test_cruzar_descarga.py

Lo que no puede fallar: un match por sha256 tiene que ganarle a uno por nombre,
y una foto que ya esta en la base no puede reportarse como 'material nuevo'.
"""
from cruzar_descarga import (MISMO_ROTO, NOMBRE, NOMBRE_FECHA, SHA, cruzar,
                             sha_sirve)


def desc(ruta, nombre, sha, fecha, bytes_=100):
    return {"ruta": ruta, "nombre": nombre, "sha256": sha, "fecha": fecha, "bytes": bytes_}


def main():
    danados = {
        r"K:\rota\DSC01.JPG": "truncado + hash distinto",
        r"K:\rota\DSC02.JPG": "truncado + hash OK",
        r"K:\rota\DSC03.JPG": "NO decodifica",
        r"K:\rota\DSC04.JPG": "truncado + hash OK",
    }
    db = {
        r"K:\rota\DSC01.JPG": {"sha": "aaa", "capture_date": "2025-10-18T10:00", "evento": "F6"},
        r"K:\rota\DSC02.JPG": {"sha": "bbb", "capture_date": "2025-10-18T11:00", "evento": "F6"},
        r"K:\rota\DSC03.JPG": {"sha": "ccc", "capture_date": "2025-10-18T12:00", "evento": "F6"},
        r"K:\rota\DSC04.JPG": {"sha": "ddd", "capture_date": "2025-10-18T13:00", "evento": "F6"},
        r"K:\sana\OTRA.JPG":  {"sha": "eee", "capture_date": "2024-01-01T00:00", "evento": "X"},
    }
    descarga = {
        # identico al hash guardado de DSC01 -> el original intacto
        "d1": desc(r"D:\baj\DSC01.JPG", "dsc01.jpg", "aaa", "2025-10-18"),
        # mismo nombre y fecha que DSC02, contenido distinto (reexportado)
        "d2": desc(r"D:\baj\DSC02.JPG", "dsc02.jpg", "zzz", "2025-10-18"),
        # mismo nombre que DSC03 pero otra fecha -> match debil
        "d3": desc(r"D:\baj\DSC03.JPG", "dsc03.jpg", "yyy", "2001-01-01"),
        # una foto que YA esta en la base (misma sha que OTRA.JPG)
        "d4": desc(r"D:\baj\OTRA.JPG", "otra.jpg", "eee", "2024-01-01"),
        # foto genuinamente nueva
        "d5": desc(r"D:\baj\NUEVA.JPG", "nueva.jpg", "nnn", "2026-02-01"),
    }

    # el sha guardado solo prueba "estaba sano" si el daño vino despues
    assert sha_sirve("truncado + hash distinto (se rompio despues de indexar)")
    assert not sha_sirve("truncado + hash OK (ya venia roto del rescate)"), \
        "si ya venia roto, la base guardo el hash del archivo ROTO"
    assert not sha_sirve("NO decodifica"), "los RAW tienen el hash refrescado"

    rec, huer, nuevas = cruzar(danados, descarga, db)
    por_ruta = {r[0]: r for r in rec}

    assert por_ruta[r"K:\rota\DSC01.JPG"][4] == SHA, "sha256 tiene que ganar"
    assert por_ruta[r"K:\rota\DSC02.JPG"][4] == NOMBRE_FECHA
    assert por_ruta[r"K:\rota\DSC03.JPG"][4] == NOMBRE, "distinta fecha = match debil"
    assert len(rec) == 3, rec

    # DSC04 no tiene candidato en la descarga
    assert [h[0] for h in huer] == [r"K:\rota\DSC04.JPG"], huer

    # El caso que destapo el humo: si el archivo YA venia roto al indexar, un
    # match por sha256 significa que bajaste la misma version rota. Eso no
    # puede contarse como recuperado.
    dan2 = {r"K:\rota\DSC05.JPG": "truncado + hash OK (ya venia roto del rescate)"}
    db2 = {r"K:\rota\DSC05.JPG": {"sha": "mmm", "capture_date": "2025-01-01T00:00",
                                  "evento": "F6"}}
    desc2 = {"x": desc(r"D:\baj\DSC05.JPG", "dsc05.jpg", "mmm", "2025-01-01")}
    r2, h2, _ = cruzar(dan2, desc2, db2)
    assert not r2, "una copia igual de rota no es un rescate"
    assert h2 and h2[0][5] == MISMO_ROTO, h2
    assert h2[0][4] == r"D:\baj\DSC05.JPG", "igual conviene decir cual era"

    rutas_nuevas = {n[0] for n in nuevas}
    assert r"D:\baj\NUEVA.JPG" in rutas_nuevas
    assert r"D:\baj\OTRA.JPG" not in rutas_nuevas, \
        "una foto que ya esta en la base no es material nuevo"
    assert r"D:\baj\DSC01.JPG" not in rutas_nuevas, \
        "el reemplazo de un dañado tampoco es material nuevo"

    # nada se pierde ni se cuenta dos veces
    assert len(rec) + len(huer) == len(danados)
    print("OK: prioriza sha256, degrada a nombre+fecha, y no inventa material nuevo")


if __name__ == "__main__":
    main()

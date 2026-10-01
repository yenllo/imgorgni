r"""
Arma una pagina HTML con los eventos que faltan nombrar, del mas grande al mas
chico, para poder decidir el nombre de un vistazo sin abrir el curador.

Nombrar 50 eventos lleva el archivo del 78% al 91%; nombrar los 300 chicos
suma 1,2%. Por eso la lista va ordenada por tamano y muestra cuanto rinde cada
uno: la idea es que pares cuando deje de valer la pena.

De cada evento: fecha, cuantas fotos, que porcentaje del archivo, sus etiquetas
de contenido reales y 5 miniaturas. Las miniaturas salen del cache que ya tiene
el curador (_thumbs_curador/<id>.jpg), asi que esto no toca K: ni la red.

    python lista_para_nombrar.py            # los 50 mas grandes
    python lista_para_nombrar.py --n 100
"""
import argparse
import base64
import html
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB = r"C:/Renzo/Proyectos/IMGorgni/photos_v2.db"
THUMBS = Path("_thumbs_curador")
SALIDA = "lista_para_nombrar.html"
CALIDAD = {"completely dark photo", "blurry unusable photo",
           "screenshot of a computer screen"}


def miniatura(pid):
    p = THUMBS / f"{pid}.jpg"
    if not p.exists():
        return None
    return base64.b64encode(p.read_bytes()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--puerto", type=int, default=5000)
    a = ap.parse_args()

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=180)
    c.row_factory = sqlite3.Row
    ev = [dict(r) for r in c.execute("""
        SELECT e.id, e.suggested_name, e.start_time, e.end_time, e.parent_id,
               (SELECT COUNT(*) FROM event_photos ep WHERE ep.event_id=e.id) n
        FROM events e
        WHERE COALESCE(e.confirmado,0)=0 AND TRIM(COALESCE(e.name,''))=''""")]
    total = c.execute("SELECT COUNT(*) FROM event_photos").fetchone()[0]
    ya = c.execute("""SELECT COUNT(DISTINCT ep.photo_id) FROM event_photos ep
                      JOIN events e ON e.id=ep.event_id
                      WHERE TRIM(COALESCE(e.name,''))<>''""").fetchone()[0]
    ev = [x for x in ev if x["n"] > 0]
    ev.sort(key=lambda r: -r["n"])
    ev = ev[:a.n]

    tags, fotos = {}, {}
    for x in ev:
        filas = c.execute("""SELECT pf.scene_tags, ep.photo_id
                             FROM event_photos ep
                             JOIN photo_features pf ON pf.photo_id=ep.photo_id
                             WHERE ep.event_id=? LIMIT 600""", (x["id"],)).fetchall()
        cnt = Counter()
        for s, _ in filas:
            try:
                for t in json.loads(s or "[]"):
                    if isinstance(t, str) and t.lower() not in CALIDAD:
                        cnt[t] += 1
            except json.JSONDecodeError:
                pass
        tags[x["id"]] = [k for k, _ in cnt.most_common(5)]
        ids = [r[1] for r in filas]
        paso = max(1, len(ids) // 5)
        fotos[x["id"]] = [i for i in ids[::paso][:5]]

    acum = ya
    piezas = []
    for i, x in enumerate(ev, 1):
        acum += x["n"]
        ini = (x["start_time"] or "")[:10]
        fin = (x["end_time"] or "")[:10]
        rango = ini + (f" → {fin}" if fin and fin != ini else "")
        ims = "".join(
            f'<img src="data:image/jpeg;base64,{m}" alt="">'
            for m in (miniatura(p) for p in fotos[x["id"]]) if m)
        pills = "".join(f"<span class=t>{html.escape(t)}</span>" for t in tags[x["id"]])
        piezas.append(f"""
<article>
  <div class=cab>
    <span class=num>{i}</span>
    <div>
      <a href="http://127.0.0.1:{a.puerto}/evento/{x['id']}" target="_blank">
        {html.escape(x['suggested_name'] or 'Evento ' + str(x['id']))}</a>
      <div class=meta>{rango} · <b>{x['n']:,}</b> fotos ·
        nombrando hasta acá llegás al <b>{100*acum/total:.1f}%</b></div>
    </div>
  </div>
  <div class=tags>{pills}</div>
  <div class=tiras>{ims}</div>
</article>""")

    doc = f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Eventos por nombrar</title>
<style>
 :root{{color-scheme:dark light}}
 body{{font:15px/1.5 system-ui,sans-serif;margin:0;padding:1rem;
   background:#14161a;color:#e8e8ea;max-width:900px;margin-inline:auto}}
 h1{{font-size:1.3rem;margin:.2rem 0}}
 .intro{{color:#9aa;margin-bottom:1.2rem}}
 article{{background:#1c1f25;border:1px solid #2b2f37;border-radius:10px;
   padding:.8rem;margin-bottom:.9rem}}
 .cab{{display:flex;gap:.7rem;align-items:flex-start}}
 .num{{background:#2b6cb0;color:#fff;border-radius:6px;padding:.1rem .5rem;
   font-weight:700;font-size:.85rem;flex:none}}
 a{{color:#7cb7ff;text-decoration:none;font-weight:600}}
 a:hover{{text-decoration:underline}}
 .meta{{color:#9aa;font-size:.85rem}}
 .tags{{margin:.5rem 0 .4rem;display:flex;flex-wrap:wrap;gap:.3rem}}
 .t{{background:#2b2f37;border-radius:99px;padding:.1rem .55rem;font-size:.78rem;color:#c3c9d4}}
 .tiras{{display:flex;gap:.35rem;overflow-x:auto}}
 .tiras img{{height:104px;border-radius:6px;flex:none}}
 @media (prefers-color-scheme:light){{
   body{{background:#f6f7f9;color:#14161a}} article{{background:#fff;border-color:#dde}}
   .meta,.intro{{color:#667}} .t{{background:#eef0f4;color:#333}} a{{color:#1a5fb4}}
 }}
</style>
<h1>Eventos por nombrar</h1>
<p class=intro>Hoy tenés el <b>{100*ya/total:.1f}%</b> del archivo en eventos con nombre puesto por vos.
Están los {len(ev)} más grandes que faltan, de mayor a menor. Cada título abre el evento
en el curador (tiene que estar corriendo). Cuando el porcentaje deje de moverse, parás.</p>
{''.join(piezas)}
"""
    Path(SALIDA).write_text(doc, encoding="utf-8")
    mb = Path(SALIDA).stat().st_size / 1024 ** 2
    print(f"{SALIDA}  ({mb:.1f} MB, {len(ev)} eventos)")
    print(f"hoy: {100*ya/total:.1f}%  ->  nombrando estos {len(ev)}: {100*acum/total:.1f}%")


if __name__ == "__main__":
    main()

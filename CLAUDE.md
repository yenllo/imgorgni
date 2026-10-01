cdc# CLAUDE.md — IMGorgni

## Delegar trabajo a Antigravity (agy)

El protocolo general está en `~/.claude/CLAUDE.md` y vale para todos los proyectos.
Acá van solo las particularidades de IMGorgni.

```bash
python C:\Users\renzo\.claude\agy_bridge.py --archivo spec_loquesea.md
```

El smoke test que validó el flujo vive acá: `spec_agy_humo.md` (plantilla de cómo se le
escribe a agy) y `agy_humo_esperado.json` (su verdad de referencia). Corrió el 13/8/2026
y dio idéntico. Las corridas quedan en `agy_log.jsonl` de este proyecto.

**Restricción propia de acá:** en toda spec, prohibí explícitamente tocar `photos_v2.db`
y sus backups. Es 3,5 GB y es la fuente de verdad de eventos, caras y tags.

### Qué NO delegar todavía

Las tandas 2 y 3 del dedup escriben en `photos_v2.db` (10.338 y 49.848 archivos, y una
muestra de 900 rutas ya tocaba 271 eventos). No son el primer trabajo para un ejecutor
nuevo. Orden: smoke test → una tarea de escritura chica y reversible → recién ahí tanda 2.

## Acceso SSH a la Pi (NAS de fotos)

Hay acceso SSH sin contraseña ya configurado a la Raspberry Pi que sirve `K:\` por Samba
(ver `RUNBOOK_NAS_PI.md`). Podés entrar vos mismo, sin pedirle nada al usuario:

```bash
ssh fotosnas
```

Usa el alias de `~/.ssh/config` (clave dedicada `id_ed25519_fotosnas`, sin passphrase) hacia
`yenllo@192.168.1.62` (hostname `fotos-nas-BP`). Si `fotosnas` no resuelve (otra máquina,
otro perfil de usuario), conectá directo por LAN o por Tailscale:

```bash
ssh yenllo@192.168.1.62       # LAN
ssh yenllo@100.78.164.81       # Tailscale (misma clave, mismo usuario)
```

La Pi es solo el servidor de archivos — nunca corre el pipeline ni `web_curador.py` (esos
viven en la notebook, `photos_v2.db` nunca se mueve de acá). Detalle completo en
`RUNBOOK_NAS_PI.md`.

## Velocidades reales — casi nunca hace falta el dock

Esta tabla estuvo mal hasta el 7/9/2026 y llevaba a pedir el dock al pedo. Los
numeros de ahora estan medidos, no estimados:

| Camino | Velocidad | Medido |
|---|---|---|
| 8TB en el puerto USB3 de la Pi | **272 MB/s** | 20/8/2026, `dd iflag=direct` |
| SSD del sistema de la Pi (`/home`, `/`) | **31-35 MB/s** | 7/9/2026 |
| Notebook <-> Pi por LAN (wifi de la notebook) | **33-56 MB/s** | 21/8/2026 |
| Notebook <-> Pi por Tailscale, fuera de casa | ~4-5 MB/s | 4/9/2026 |
| Dock USB3 colgado de la notebook | ~200 MB/s | |

Dos cosas que explican todo lo demas:

- **El SSD del sistema esta en un puerto USB2 y ahi se queda.** El chip JMS578 de
  su gabinete tiene bugs con UAS en USB3 en la Pi y ademas se alimenta del bus:
  probado tres veces, no bootea en el azul. Todo lo que se escriba en `/home`
  —el cache de la galeria, las derivadas— topa a 35 MB/s. Lo que tiene que ir
  rapido va al 8TB, que si esta en USB3.
- **La red de la Pi ya es gigabit** desde el 22/8/2026 (era un puerto malo del
  modem, no el cable). El cuello que queda es el **wifi de la notebook**, que no
  tiene puerto Ethernet. Por eso 33-56 y no 110.

**Casi nunca pidas el dock.** La Pi sola lee a 272 MB/s: el SHA256 de los 2,5 TB
de la Fase 1 son ~2,6 horas leyendo local, no dias. Pedilo solo si hay que mover
cientos de GB *hacia* la notebook y el tiempo de pared importa.

Ante la duda, calcula con el numero del camino que vas a usar, no con el peor.
Ver `RUNBOOK_NAS_PI.md` y la memoria `pi-puertos-usb-velocidades`.

## Autocull integrado en el curador

Cada pagina de evento tiene el boton "★ Buscar las mejores (Autocull)". Corre
Autocull sobre los JPEG del evento, muestra el avance y al terminar pone
estrellas en cada miniatura, con filtros por cantidad de estrellas.

- `autocull_runner.py` (en este proyecto) es el puente. Se ejecuta **con el python
  del venv de Autocull** (`C:\Renzo\Proyectos\Autocull\venv\Scripts\python.exe`)
  porque Autocull tiene sus propias dependencias pesadas. Va escribiendo el avance
  a `_autocull_curador/<eid>.json`, que el curador poletea.
- Se le agrego a Autocull un parametro `files=` en `run_pipeline()` para pasarle una
  lista explicita: las fotos de un evento viven desparramadas en varias carpetas de
  K:, no forman una carpeta.
- Las estrellas salen de `final_score`, que es el **percentil dentro de la propia
  sesion**: "5 estrellas" es el top de ESE evento, no una nota absoluta.
- Ojo con el dominio: Autocull esta afinado para **futbol nocturno** y su mejor
  señal es la pelota (ancla quien es el sujeto). En deportes anda como fue diseñado;
  en baile/concierto/presentacion degrada a filtros genericos de calidad
  (nitidez, exposicion, ojos cerrados), que igual sirven pero rinden menos.

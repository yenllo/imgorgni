# RUNBOOK — NAS con Raspberry Pi 4 para K:

Objetivo: el disco de 7,28 TB deja de colgar de la notebook y pasa a vivir en una Pi,
accesible como `K:\` en casa y desde la facultad. La notebook sigue siendo la que corre
el pipeline y la que tiene `photos_v2.db`.

**Stack:** Raspberry Pi OS Lite (64-bit) + Samba a mano + Tailscale. Sin OMV, sin UI.

## Datos de tu hardware (verificados el 2026-08-04)

| Qué | Detalle |
|-----|---------|
| HDD grande | Disco 1, GPT, 7,28 TB, label `FirstDriveK`, NTFS. **K: es la partición 2**, no la 1 |
| Bridge del dock del HDD | `VID_152D PID_2578` (JMicron, modo UAS) |
| Boot | **SSD de 240 GB**, confirmado con `findmnt /` → `/dev/sda2` |
| microSD | **Corrección 2026-08-05: sigue haciendo falta.** La Pi no arranca sin la SD puesta, aunque `/` esté en la SSD — es el boot order del EEPROM (prueba SD primero). Dejarla puesta es gratis y sin riesgo; sacarla del todo pide `raspi-config` → Advanced Options → Boot Order → USB primero |
| Ocupado en K: | 3,98 TB de 7,28 TB — 2,5 TB son las 224.980 fotos vivas de la base |
| Tailscale IP | `100.78.164.81`, confirmado 2026-08-05 |
| Disco (SMART) | Toshiba HDWG780UZSVA, 8TB, 7200rpm. Chequeado 2026-08-05: 0 reallocated, 0 pending, PASSED, 125h de uso, 31°C |

## Nota: el SSD tardó en arrancar (resuelto el 2026-08-04)

Las primeras veces el SSD no bootaba — LED verde parpadeando parejo e infinito, sin
cambiar de patrón (no es un código de error; esos vienen agrupados N-M con pausa. Era el
bootloader en loop, sin encontrar nada legible). Se solucionó — probablemente por alguna
combinación de: actualizar el bootloader (`rpi-eeprom-update -a`), cambiar de canal de
firmware, revisar el switch UAS/BOT del dock, o el cable. Confirmado con `lsblk` +
`findmnt /` que `/` está en `/dev/sda2` (el SSD), no en `mmcblk0` (la SD).

Si algún día vuelve a fallar el boot del SSD después de una regrabación, la microSD queda
como plan B conocido: arranca sin problema y sirve para diagnosticar sin monitor.

---

# Parte A — En Windows, antes de mover nada

## 1. Apagar Fast Startup

Si Windows hiberna en vez de apagar, deja el NTFS con el dirty bit puesto y Linux te lo
monta **solo lectura, sin avisar**. Es el error más confuso de todo este proceso.

```bash
powercfg /h off
```

(PowerShell como administrador.)

## 2. Backup de la base

La base no se mueve, pero vas a tocar el mapeo de K:. Barato:

```bash
copy photos_v2.db photos_v2.db.backup-antes-nas
```

## 3. Confirmar que el dock tiene fuente propia

Si el dock se alimenta del puerto USB de la Pi, vas a tener corrupción intermitente: la Pi
reparte ~1,2 A entre los 4 puertos y un HDD de 3,5" solo ya pide más que eso. El tuyo es de
escritorio con fuente, pero confirmalo antes de seguir.

## 4. Expulsar K: limpio

Bandeja del sistema → "Quitar hardware de forma segura" → expulsar el disco de 7,28 TB.
**No lo desenchufes a lo bruto.** Si el dirty bit queda puesto, el Paso 15 falla.

---

# Parte B — Grabar el sistema

## 5. Raspberry Pi Imager

Descargalo en la notebook. Conectá el SSD de 240 GB por USB.

- Dispositivo: **Raspberry Pi 4**
- SO: **Raspberry Pi OS Lite (64-bit)** (Bookworm — trae kernel 6.x, lo necesitás para `ntfs3`)
- Almacenamiento: el SSD de 240 GB

## 6. Opciones avanzadas (el ⚙️) — no lo saltees

Configurando esto acá, la Pi nunca necesita monitor ni teclado.

**Lo que quedó grabado el 2026-08-04** (leído del `user-data` de la partición `bootfs`):

| Opción | Valor |
|--------|-------|
| Hostname | `fotos-nas-yenll` → resuelve como `fotos-nas-yenll.local` |
| Usuario | **`yenllo`** (con sudo, shell bash, password auth habilitada) |
| SSH | Habilitado, con contraseña (`ssh_pwauth: true`) |
| WiFi | No configurado ✅ |
| Zona horaria | `America/Santiago`, teclado `us` |
| Extras | `avahi-daemon` (mDNS) y `rpi-connect` con auth key |

`yenllo` es el primer usuario, así que va a ser **uid 1000**. Confirmalo con `id` en el Paso 9:
el fstab del Paso 14 depende de eso.

De acá en adelante el runbook usa `yenllo`. Si alguna vez regrabás con otro usuario, cambiá
los Pasos 9, 14, 16, 17 y 19, más el `/user:` de `montar_K.ps1`.

## 7. Grabar

Confirmá que estás borrando el SSD de 240 GB y no el de 7,28 TB. Mirá el tamaño dos veces.

---

# Parte C — Primer arranque

## 8. Cablear (sin el HDD todavía)

Menos piezas en el primer arranque = menos cosas que pueden fallar.

- SSD de 240 GB → **puerto USB3 (azul)**
- Ethernet → al router
- **HDD grande: todavía no**
- Fuente oficial 5V/3A USB-C. Las genéricas baratas son la causa #1 de "la Pi se reinicia sola".

Encendé. Esperá ~90 segundos.

## 9. Entrar por SSH

Siempre **por IP**, no por hostname: la reserva DHCP es por MAC y sobrevive a regrabar el SSD,
mientras que el nombre cambia cada vez que reconfigurás el Imager (y el router te lo muestra
cortado o cacheado del arranque anterior).

```bash
ssh-keygen -R 192.168.1.62      # la host key cambia cada vez que regrabás
ssh yenllo@192.168.1.62
```

Adentro, para confirmar nombre y uid:

```bash
hostnamectl; id                  # id tiene que decir uid=1000
```

**Si no bootea:** conectá teclado y monitor una vez → `sudo raspi-config` → Advanced Options →
Boot Order → USB Boot. Suele venir ya configurado en Pi 4 recientes.

## 10. Reservar la IP en el router — YA HECHO

`192.168.1.62`, MAC `e4:5f:01:0a:51:27`, puerto LAN2, lease `Infinity`. Confirmado el
2026-08-04 en el panel del router. Como la reserva es por MAC, sigue valiendo después de
regrabar. Pasá al Paso 11.

## 11. Actualizar

```bash
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

Volvé a entrar y verificá el uid:

```bash
id    # tiene que decir uid=1000(yenllo) gid=1000(yenllo)
```

---

# Parte D — El disco grande

## 12. Conectarlo y ver si UAS se porta bien

Apagá la Pi, conectá el dock al **otro puerto azul**, prendé primero el dock y después la Pi:

```bash
sudo poweroff
```

Ya con todo encendido y de vuelta por SSH:

```bash
lsblk -f
```

Tenés que ver el disco de 7,3 TB con su partición de 7,28 TB etiquetada `FirstDriveK`.
Anotá el nombre real (`/dev/sda2`, `/dev/sdb2`, lo que salga).

**Ahora la prueba de fuego.** Tus dos gabinetes son JMicron en modo UAS, que es el combo con
más bugs documentados en Raspberry Pi: bajo carga da errores de I/O y desconexiones que se ven
igual que un problema de alimentación. Pero deshabilitar UAS también cuesta velocidad, así que
**medí antes de arreglar lo que quizás no está roto**:

```bash
sudo dd if=/dev/sdX2 of=/dev/null bs=1M count=20000 status=progress   # ~20 GB, unos 3 min
dmesg | grep -iE 'reset|uas|error'
```

- **Sin errores en `dmesg`** → dejalo así, tenés UAS y es más rápido. Seguí al Paso 13.
- **Aparecen `reset SuperSpeed`, `uas_eh_device_reset` o errores de I/O** → aplicá el quirk:

  ```bash
  sudo nano /boot/firmware/cmdline.txt
  ```

  Es **una sola línea**. Agregá al final, separado por un espacio, sin meter saltos de línea:

  ```
  usb-storage.quirks=152d:2578:u,152d:0578:u
  ```

  Reboot y verificá que quedó: `cat /proc/cmdline`. Repetí el `dd` — ahora tiene que salir limpio.

### Resultado real (2026-08-05)

Disco detectado como `sda2`. `dd` dio **35,7 MB/s parejo**, y el `dmesg` salió **limpio**
(sin `reset`/`uas`/`error`) — o sea que UAS no tenía ningún bug real, el problema era pura
velocidad. **35 MB/s es la firma clásica de USB2 (480M), no de USB3 (5000M)** — confirmado
con `lsusb -t`. La causa: el cable que se usó para conectar el dock a la Pi era **USB-B a
USB-C**, no USB-A a USB-B. Dato importante que no estaba anotado antes: **el puerto USB-C
de la Pi 4 es solo alimentación, no lleva datos** — el dock tiene que llegar sí o sí a uno
de los 4 puertos USB-A (preferí los azules/USB3).

Se aplicó el quirk de todas formas (no hacía falta, el `dmesg` ya estaba limpio) y no
cambió nada la velocidad ni la latencia por archivo — confirma que el cuello es el cable,
no el protocolo. Se revirtió sacando la línea de `cmdline.txt` y volvió a `uas` sin drama.

**Pendiente:** cable USB-A a USB-B 3.0 correcto (comprado 2026-08-05, llega esa tarde).
Cuando llegue, repetir `dd` + `lsusb -t` — tiene que dar `5000M` y bastante más de 35 MB/s.

## 13. Herramientas de NTFS

```bash
sudo apt install -y ntfs-3g smartmontools
```

`ntfs-3g` lo instalás por `ntfsfix`, pero **no vas a montar con él**: es FUSE, un solo hilo,
y en la Pi 4 te clava un core y topea en ~40-60 MB/s. El montaje va con el driver del kernel.

## 14. Montar con `ntfs3`

```bash
sudo blkid /dev/sdX2      # copiá el UUID
sudo mkdir -p /mnt/fotos
sudo nano /etc/fstab
```

Agregá al final (una línea, con tu UUID):

```
UUID=TU-UUID-ACA  /mnt/fotos  ntfs3  defaults,nofail,uid=1000,gid=1000,umask=022  0  0
```

`nofail` es importante: si el disco no está listo al bootear, la Pi arranca igual en vez de
quedarse colgada esperando en un arranque sin monitor.

```bash
sudo mount -a
```

## 15. Verificar que montó en **lectura-escritura**

Este es el paso donde se detecta el dirty bit. No lo saltees:

```bash
mount | grep fotos          # tiene que decir (rw,...), NO (ro,...)
touch /mnt/fotos/_test && rm /mnt/fotos/_test && echo "ESCRITURA OK"
ls /mnt/fotos               # tus carpetas de siempre
df -h /mnt/fotos            # ~7,3T, ~4T usados
```

**Si montó `ro`** — el NTFS quedó sucio:

```bash
sudo umount /mnt/fotos
sudo ntfsfix -d /dev/sdX2
sudo mount -a
```

Si `ntfsfix` no alcanza, volvé el disco a Windows, corré `chkdsk K: /f`, expulsalo limpio y
reintentá. No fuerces el montaje rw sobre un NTFS sucio.

**Si `ntfs3` da problemas raros** (es más nuevo que `ntfs-3g`), cambiá el tipo en fstab a
`ntfs-3g` y remontá. Perdés velocidad, ganás madurez.

**Confirmado 2026-08-05:** montó `rw` a la primera, escritura OK, árbol completo (verificado
contra la base con `verificar_nas.py`, ver Paso 20 — el único problema que apareció ahí
terminó siendo de Windows, no del mount, ver más abajo).

---

# Parte E — Compartir por red

## 16. Samba

```bash
sudo apt install -y samba
sudo smbpasswd -a yenllo
```

La contraseña de Samba es **aparte** de la del sistema. Podés poner la misma.

## 17. El share

```bash
sudo tee -a /etc/samba/smb.conf > /dev/null << 'EOF'

[Fotos]
   path = /mnt/fotos
   browseable = yes
   read only = no
   guest ok = no
   valid users = yenllo
   hosts allow = 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12 100.64.0.0/10 127.0.0.1
EOF
```

`hosts allow` limita el share a redes privadas + el rango de Tailscale (`100.64.0.0/10`).
Con `hosts allow` puesto, todo lo que no matchea queda denegado — no hace falta `hosts deny`.
Nunca expongas Samba directo a internet; para eso está Tailscale.

```bash
testparm -s        # valida la sintaxis antes de reiniciar
sudo systemctl restart smbd
```

---

# Parte F — Volver a tener K:

## 18. Liberar la letra K: en Windows

El disco ya no está conectado a la notebook, así que la letra tendría que estar libre.
Verificá en Administración de discos que ninguna otra unidad se haya quedado con la K.

## 19. Mapear

```bash
net use K: \\192.168.1.62\Fotos /user:yenllo /persistent:yes
```

Te pide la contraseña de Samba del Paso 16.

## 20. Verificar contra la base real

Este es el chequeo que importa: no alcanza con que `dir K:\` liste algo, tienen que existir
las rutas exactas que la base tiene guardadas.

```bash
python verificar_nas.py
```

Las 224.980 filas de `files` tienen `file_path` empezando en `K:\` — el 100%. Si la letra
coincide, el pipeline entero sigue andando sin tocar una línea de código.

## 21. El detalle de los procesos elevados

Una unidad de red mapeada **no existe para procesos elevados** (UAC usa un token distinto).
Si algún día corrés un script como administrador, `K:\` no va a estar.

Opción A (gratis): no corras el pipeline elevado. Nunca lo necesitaste.

Opción B: crear `EnableLinkedConnections` (DWORD = 1) en
`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager` y reiniciar. Es un cambio de
configuración del sistema — hacelo vos si lo querés.

**Confirmado 2026-08-05 — esto pasó de verdad, aplicá la Opción B directamente:**
`net use K:` se corrió en una PowerShell elevada, y después `python verificar_nas.py` (no
elevado) fallaba con "50/50 rutas no existen" — con archivos *distintos* cada corrida, todos
confirmados presentes con `Test-Path`/`dir` en la misma sesión que sí veía `K:`. La pista es
esa: si `dir`/`Test-Path` ven el archivo pero Python (u otro proceso no elevado/elevado
distinto) dice que no existe, es el token, no el disco ni Samba. `reg add` de una línea:

```powershell
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager" /v EnableLinkedConnections /t REG_DWORD /d 1 /f
```

Reiniciar Windows, volver a mapear `K:` si hace falta, listo — se soluciona para siempre.

---

# Parte G — Acceso remoto

## 22. Tailscale en la Pi

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Te da una URL, la abrís en el navegador y autenticás. Después:

```bash
tailscale ip -4     # anotá la 100.x.x.x
```

## 23. Desactivar la expiración de clave

En [login.tailscale.com](https://login.tailscale.com) → Machines → `fotos-nas-yenll` → **Disable key
expiry**. Sin esto, a los 90 días la Pi se cae sola del tailnet y no vas a entender por qué.

## 24. Tailscale en la notebook

Instalá la app de escritorio, mismo login.

## 25. Mapeo automático LAN / Tailscale

En casa conviene ir directo por LAN (gigabit real, ~110 MB/s). Por Tailscale, la Pi 4 hace
WireGuard por software y topea en ~30-40 MB/s aunque estés en la misma red. Como solo tenés
una letra K:, usá el script:

1. Editá `montar_K.ps1` y poné la IP `100.x.x.x` del Paso 22.
2. Cada vez que cambies de red:

```bash
powershell -File montar_K.ps1
```

Prueba LAN primero, cae a Tailscale si no está. No necesita permisos de administrador.

**Ojo con el número que imprime** (`LAN ~110 MB/s` salió el 2026-08-05): es un test rápido
que puede pegarle a caché, no una medición real de disco. Para el número que importa, confiá
en `python verificar_nas.py` (Paso 20), que sí lee de punta a punta.

---

# Parte H — Confiabilidad

## 26. SMART sobre USB

```bash
sudo smartctl -a -d sat /dev/sdX
```

Sin `-d sat` el bridge JMicron no pasa los comandos ATA y te dice "Unknown USB bridge".
Si `sat` tampoco anda, probá `-d sntjmicron`.

Mirá `Reallocated_Sector_Ct` y `Current_Pending_Sector`: si cualquiera de los dos empieza a
subir, el disco te está avisando con tiempo.

**Confirmado 2026-08-05:** `-d sat` funcionó directo, no hizo falta `sntjmicron`. Disco sano
(ver tabla de arriba).

---

# Parte I — Panel web (opcional, agregado 2026-08-05)

Para no depender solo de SSH: Cockpit (admin general — disco, CPU/RAM, logs, terminal) más
Cockpit Navigator (explorador de archivos, se integra como pestaña dentro de Cockpit).

## 27. Cockpit

```bash
sudo apt install -y cockpit
```

Se activa solo por systemd socket, puerto 9090. Entrás por `https://192.168.1.62:9090` (o
por Tailscale `https://100.78.164.81:9090`), aceptás el warning del certificado autofirmado,
login con `yenllo`.

## 28. Cockpit Navigator

Se descartó **File Browser** (`filebrowser/filebrowser`): el proyecto se discontinúa, repo
se archiva el 1 de septiembre de 2026, sin más releases. Cockpit Navigator es un plugin
mantenido que se integra directo en Cockpit — una sola herramienta en vez de dos.

```bash
wget https://github.com/45Drives/cockpit-navigator/releases/download/v0.5.10/cockpit-navigator_0.5.10-1focal_all.deb
sudo apt install ./cockpit-navigator_0.5.10-1focal_all.deb
```

(Si esa versión ya no existe, fijate la última en las releases del repo.) Aparece como
"Navigator" en el menú de Cockpit — apuntalo a `/mnt/fotos`.

---

# Acceso SSH sin contraseña desde la notebook (agregado 2026-08-05)

Para que scripts del pipeline/curador puedan hablar con la Pi sin pedir contraseña cada vez.
Clave dedicada (no la general de GitHub), sin passphrase:

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_ed25519_fotosnas" -N '""' -C "renzo-notebook-fotosnas"
Get-Content "$env:USERPROFILE\.ssh\id_ed25519_fotosnas.pub" | ssh yenllo@192.168.1.62 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Y un alias en `~/.ssh/config` (de Windows, `C:\Users\<vos>\.ssh\config`):

```
Host fotosnas
    HostName 192.168.1.62
    User yenllo
    IdentityFile ~/.ssh/id_ed25519_fotosnas
```

De ahí en más, `ssh fotosnas` conecta directo, sin contraseña.

---

# Expectativa realista

**Baseline medido el 2026-08-04, con K: todavía por USB3 directo:** `verificar_nas.py` da
**24 MB/s, 37 ms por archivo** leyendo 1 MB de 50 archivos al azar. O sea: en acceso aleatorio
el cuello ya es el seek del HDD, no el bus. El USB3 nunca fue el límite.

Eso cambia el cálculo a tu favor:

| Escenario | Lectura secuencial | Acceso aleatorio (lo que hace el curador) |
|-----------|--------------------|-------------------------------------------|
| Antes, USB3 directo | ~150 MB/s | 24 MB/s ← medido |
| En casa, LAN gigabit | ~110 MB/s | parecido: sigue mandando el seek |
| Por Tailscale | ~30-40 MB/s (WireGuard por software en la Pi 4) | peor: se suma latencia |
| Desde la facultad | Lo que suba tu internet de casa. Tailscale no hace magia | |

**Rescan completo de Fase 1** (SHA256 de los 2,5 TB en 224.980 archivos): entre 6 y 9 h, y
va a dar parecido por LAN que por USB, porque en los dos casos el que marca el ritmo es el
disco. No lo lances un martes a la tarde, pero no es la catástrofe que parecía.

Corré `python verificar_nas.py` después de migrar y comparalo contra esos 24 MB/s.

**Medido de verdad el 2026-08-05, por LAN con el cable USB2 (no el correcto todavía):**
4-5 MB/s, 170-216 ms por archivo — bastante peor que el baseline de arriba. Es 100%
explicable por el cable (35,7 MB/s de techo en vez de ~150 MB/s de un USB3 real, ver Paso
12), no señal de que la LAN o Samba anden mal. Repetir la medición cuando llegue el cable
correcto — la expectativa es volver a acercarse a los 24 MB/s / 37 ms del baseline original.

**Lo bueno:** `_thumbs_curador` y `_face_thumbs_curador` viven en la carpeta del proyecto, o
sea locales en la notebook. Navegar el curador desde la facultad va a ir rápido — solo pega a
la red cuando abrís un original. El diseño ya te favorece sin cambiar nada.

---

# Lo que la Pi NO hace

- **No corre la Fase 2.** Necesita DirectML y tu GPU. Imposible en la Pi.
- **No corre una segunda copia de `web_curador.py`.** SQLite sobre un filesystem de red tiene
  problemas de locking documentados por los propios autores de SQLite. `photos_v2.db` vive
  solo en la notebook (`C:\Renzo\Proyectos\IMGorgni\photos_v2.db`, como ya dice `config_K.json`)
  y el curador se corre siempre desde ahí.
- La Pi es pura y exclusivamente el servidor de archivos.

---

# ANTES de desconectar el disco de la Pi — desmontalo

**Esto ya rompio el NTFS una vez (2026-08-05).** Se saco el disco en caliente,
con Samba sirviendolo, y quedo una escritura a medias:

```
Buffer I/O error on dev sda2, logical block 786432, lost sync page write
```

Al volver a conectarlo, el disco entro como `/dev/sdc` (letra nueva, porque la
vieja seguia tomada por el montaje muerto) y `mount` fallo con:

```
$MFTMirr does not match $MFT (record 3).
Failed to mount '/dev/sdc2': Input/output error
```

**Y volvio a pasar el 2026-08-12**, esta vez por una mudanza: se corto la energia
con el disco montado y el NTFS quedo con el dirty bit. `ntfs3` se nego a montarlo
(`volume is dirty and "force" flag is not set!`) hasta reparar desde Windows.

## El procedimiento, hoy

`sudo umount /mnt/fotos` **ya no alcanza**: desde que `/mnt/fotos` tiene
`x-systemd.automount` en el fstab, cualquier cosa que toque la carpeta lo vuelve
a montar sola. Si desmontas y Samba pasa por ahi un segundo despues, el disco se
desenchufa montado igual.

Por eso hay dos scripts en `/usr/local/bin/` que hacen la secuencia entera:

**1. En Windows, soltar la unidad** (o doble clic en `soltar-K.bat` del escritorio):

```
net use K: /delete
```

**2. En la Pi:**

```bash
sudo expulsar-disco
```

Para la galeria y Samba, desarma el automount, desmonta y **verifica**. Si algo
quedo agarrando el disco te dice que proceso es, vuelve a levantar los servicios
y avisa `NO DESENCHUFES EL DISCO`. Solo con el cartel de `LISTO` es seguro sacarlo.

**3. Recien ahi, desenchufar.**

**Para devolverlo**, enchufar y:

```bash
sudo montar-disco
```

Monta, levanta Samba y la galeria, y lista como quedo cada servicio. Si no monta,
muestra el error del kernel y, si es el dirty bit, escribe en pantalla el comando
de reparacion.

## Hacerlo desde afuera de casa

Cockpit escucha en el `:9090` y la Pi esta en Tailscale, asi que con Tailscale
prendido en el celular:

```
https://100.78.164.81:9090
```

Menu izquierdo → **Terminal** → `sudo expulsar-disco`. Sirve para pedirle a
alguien en casa que solo tire del cable: la expulsion la hacés vos, remoto, y
esperás el cartel de `LISTO` antes de avisarle.

**Nunca exponer Cockpit por Caddy a internet.** Es acceso root a la maquina;
Tailscale es la via correcta.

## Como se recupero (por si vuelve a pasar)

1. Confirmar que **no es hardware** antes de tocar nada:
   ```bash
   sudo smartctl -a -d sat /dev/sdX | grep -iE 'overall|Reallocated|Pending|Uncorrectable|CRC'
   dmesg | grep -iE 'sdX|I/O error|reset'
   ```
   En este caso salio todo limpio (PASSED, 0 reasignados, 0 pendientes, 0 CRC),
   asi que era daño logico y no fisico.
2. Soltar el montaje muerto y reparar:
   ```bash
   sudo umount -l /mnt/fotos
   sudo ntfsfix -d /dev/sdX2
   sudo mount /mnt/fotos
   ```
   `ntfsfix` corrigio 6 registros del `$MFTMirr`, vacio el `$LogFile` y listo.
   Tomo segundos. **No se perdio ni una fila de la base** (203.494 antes y
   despues) ni un archivo.
3. Si `ntfsfix` no alcanza: llevar el disco a Windows y `chkdsk K: /f`.

**No forzar el montaje rw sobre un NTFS inconsistente** — ahi si se pierde data.

## Dos trampas del fstab que apareceron en el camino

- **`noauto`** se habia colado en la linea: `mount -a` la saltea en silencio (y
  el disco no monta al arrancar). Hay que montar por punto de montaje
  (`sudo mount /mnt/fotos`) o sacar el `noauto`.
- **tipo `auto`** en vez de `ntfs3`: al remontar, `auto` eligio **ntfs-3g**
  (aparece como `fuseblk` en `mount`), que es FUSE monohilo y bajo la velocidad
  de 8 a 5 MB/s. La linea correcta es:
  ```
  UUID=60D260D2D260ADCA /mnt/fotos ntfs3 defaults,nofail,uid=1000,gid=1000,umask=022 0 0
  ```

---

# Si algo falla

| Síntoma | Causa casi segura |
|---------|-------------------|
| Monta pero no deja escribir | Dirty bit de NTFS → Paso 15 |
| Desconexiones / I/O errors bajo carga | UAS de JMicron → quirk del Paso 12 |
| La Pi se reinicia sola | Fuente genérica, o el dock alimentándose de la Pi |
| `\\192.168.1.62\Fotos` no aparece | `hosts allow` no cubre tu subred → `testparm -s` |
| Andaba y dejó de andar a los ~3 meses | Expiró la clave de Tailscale → Paso 23 |
| `K:` no existe en un script | Proceso elevado → Paso 21 |
| `verificar_nas.py` dice que faltan rutas random, distintas cada corrida, pero `dir`/`Test-Path` sí las ven | Token elevado ≠ token del script → Paso 21, aplicar `EnableLinkedConnections` directo, no perder tiempo con reintentos |
| `dd` da ~35 MB/s parejo y `dmesg` sale limpio (sin reset/uas/error) | No es bug de UAS, es cable USB2 en vez de USB3 → revisar que sea USB-A a USB-B real, y que no sea el puerto USB-C de la Pi (ese es solo alimentación) |
| `uas_eh_abort_handler` + `I/O error` + la Pi se reinicia en bucle | **Mirar qué se enchufó antes de culpar al driver.** El 12/8/2026 fue un teclado RGB (HyperX, ~400 mA de LEDs) comiéndose el presupuesto USB con dos discos colgados. Nada de periféricos con luces en la Pi |
| Arranca "otro sistema": otra clave de host SSH, sin Tailscale, sin `~/galeria` ni `~/dedup` | No se perdió nada: no pudo leer el SSD y el orden de arranque `f14` cayó a una **SD vieja**. Arreglar la alimentación del SSD y vuelve solo |
| `wrong fs type, bad option, bad superblock` al montar el 8TB | Dirty bit tras un corte. Desde Windows: `Repair-Volume -DriveLetter K -OfflineScanAndFix` (si `chkdsk.exe` da "Acceso denegado" aun elevado, este cmdlet lo esquiva) |
| La Pi está en el login pero no responde ni por LAN ni por Tailscale | Si **las dos** vías fallan no es ruteo, está fuera de la red: cable de ethernet flojo. Mirar los LEDs del conector |

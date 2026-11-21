# Plan: probar Linux en arranque dual sin perder Windows

Escrito el 18/11/2026. Objetivo: saber, con una fecha real, si Lightroom Classic y tu flujo de trabajo funcionan
en Linux en tu notebook. Windows se queda tal cual: el CoD y los otros juegos con anti-cheat siguen ahí.

## La decisión: Debian 13 "trixie" (elegido por Renzo el 18/11)

| | Debian 13 | Parrot Home 7.3 | Fedora 44 |
|---|---|---|---|
| App de escritorio de Claude (Chat, Cowork, Code) | **Oficial** (Debian 12+, apt de Anthropic) | "Puede funcionar", no probado oficialmente | No: solo el CLI |
| Kernel | 6.12 LTS (más nuevo en backports) | 7.0 | 6.x reciente |
| Tu wifi Realtek RTL8922AE | Driver desde ~6.11: debería andar | Hay un reporte de que se rompió en kernel 7.0 | — |
| Lightroom (AppImage v1.2.0, trae su Wine) | Sí | Sí | Sí, donde más se probó |

**Va Debian 13.** El look tipo bspwm (bspwm + sxhkd + polybar + rofi) se instala encima, no depende de Parrot.
Antes de instalar, probar el pendrive en modo **live** (sin tocar el disco): que conecte la wifi y que
`vulkaninfo --summary` muestre la Radeon 860M.

**Imagen lista:** `Descargas\Linux\debian-live-13.7.0-amd64-kde.iso` (4,19 GB), bajada el 18/11 y verificada: SHA-256
igual al oficial y firma válida de la clave de CDs de Debian.

## Tus chats de Claude Code no se pierden

- Con arranque dual, **Windows sigue con todo**: la app y sus sesiones quedan donde están.
- Cada sesión de la pestaña Code vive como archivo en `C:\Users\renzo\.claude\projects\<carpeta del proyecto>\<id>.jsonl`,
  y la memoria del proyecto en `...\memory\`. Hoy son **422 sesiones en 90 carpetas, 2,65 GB** (18/11/2026).
- En Linux se copian a `~/.claude/projects/`. La carpeta se llama según la ruta del proyecto: si en Linux el proyecto
  queda en `/home/renzo/Renzo/Proyectos/IMGorgni`, la carpeta se renombra a `-home-renzo-Renzo-Proyectos-IMGorgni`.
  Con eso `claude --resume` las lista y las retoma. La memoria (`memory/`) viaja igual.
- Lo que no viaja: la barra lateral de la app (títulos, grupos, fijados). Los chats normales de claude.ai están en tu
  cuenta y aparecen solos en cualquier equipo.
- Esto hay que probarlo en el paso 6: no está documentado por Anthropic para la app de Linux.

## Resultado del modo live (19/11/2026): pasa

Informe completo en `K:\_linux_prueba\informe_linux.txt`. La GPU acelera (Vulkan con RADV, Mesa 25.0.7), la wifi
da 250 Mbps, y audio, cámara, touchpad, Bluetooth, brillo y suspensión funcionan. Lo único: la wifi escribe
`failed to wait RF TSSI` cada pocos minutos sin cortarse. Si después de instalar notas cortes, prueba esto y reinicia:

```bash
echo -e "options rtw89_pci disable_aspm_l1=y disable_aspm_l1ss=y\noptions rtw89_core disable_ps_mode=y" | sudo tee /etc/modprobe.d/rtw89.conf
```

Si sigue, el kernel y el firmware de `trixie-backports` son más nuevos. El pendrive se graba con **Rufus** (copiar
los archivos no arrancó).

## Antes de empezar (en Windows, 15 min)

1. **Respaldo:** las fotos de trabajo ya están en K: verificadas (limpieza del 18/11). Nada más que respaldar.
2. **Cifrado:** Configuración → Privacidad y seguridad → Cifrado de dispositivo. Si está activo, anota la clave de
   recuperación (account.microsoft.com/devices/recoverykey) y suspende la protección.
3. **Inicio rápido: hoy está ACTIVADO en tu notebook (revisado el 18/11).** Panel de control → Opciones de energía →
   "Elegir el comportamiento de los botones" → desmarcar "Activar inicio rápido". Si queda prendido, Linux puede
   dañar el disco de Windows al leerlo.
4. **Pendrive** de 8 GB o más (se borra entero). Grabar la imagen con Rufus o balenaEtcher.
5. **Menú de arranque:** tu notebook es un Lenovo (modelo 83JY): **F12** (o Fn+F12) al prender, o el botón Novo si
   lo tiene. La BIOS es con F2. Secure Boot puede quedar prendido.

## Paso 1: hacerle espacio a Linux (sin formatear nada)

Disco: Samsung 1 TB, C: con 361 GB libres. **Partir con 80 GB** (se le agrega más después, ver "Agrandar Debian").
Administración de discos (`diskmgmt.msc`) → clic derecho en C: → Reducir volumen → `81920` MB.
Si no deja tanto, se apaga un rato la memoria virtual, se reinicia y se reintenta.

## Paso 2: instalar Debian en ese espacio, sobre LVM (para poder agrandarlo)

1. Arrancar desde el pendrive (F12) → en el menú, **"Graphical install"** (el instalador clásico de Debian).
2. Idioma, teclado latinoamericano, wifi Renzo_5G, usuario y clave (anótala). Cuando pregunte por firmware no libre,
   aceptar: ahí viene el de tu wifi.
3. En "Particionado de discos" elegir **"Manual"**. **No borrar ni formatear ninguna partición de Windows.**
   1. En la partición de 200 MB (`nvme0n1p1`, FAT32): "Utilizar como: **partición del sistema EFI**", **sin formatear**.
   2. En el "ESPACIO LIBRE" de 80 GB: crear una partición nueva con todo ese tamaño → "Utilizar como: **volumen físico
      para LVM**".
   3. Arriba del menú: **"Configurar el Gestor de Volúmenes Lógicos (LVM)"** → escribir los cambios → crear grupo de
      volúmenes `debian-vg` con esa partición → crear volumen lógico `root` con todo el tamaño → Terminar.
   4. En el volumen lógico `root`: "Utilizar como: **ext4**", punto de montaje **`/`**.
   5. Sin partición de intercambio (swap): con 32 GB de RAM no hace falta. Si pregunta, decir que continúe sin ella.
   6. Revisar que en la lista las particiones de Windows (p2 de 16 MB y p3 NTFS) digan que **no** se formatean, y
      "Finalizar el particionado".
4. Si la EFI de 200 MB no alcanza (lo avisa al instalar el cargador), crear dentro de los 80 GB una EFI nueva de 600 MB
   y dejar la de Windows intacta.
5. Escritorio: **KDE Plasma**. Al reiniciar aparece el menú GRUB para elegir Debian o Windows.

## Agrandar Debian más adelante (quitándole a Windows)

1. En Windows: Administración de discos → Reducir volumen en C: (por ejemplo 51200 MB = 50 GB).
2. En Debian (funciona con el sistema andando, sin reiniciar):
   ```bash
   sudo cfdisk /dev/nvme0n1          # "Free space" → New → todo el tamaño → Type: Linux LVM → Write
   lsblk                              # ver el número de la partición nueva, por ejemplo nvme0n1p6
   sudo pvcreate /dev/nvme0n1p6 && sudo vgextend debian-vg /dev/nvme0n1p6
   sudo lvextend -r -l +100%FREE /dev/debian-vg/root
   df -h /                            # ya debería mostrar el tamaño nuevo
   ```
Devolverle espacio a Windows también se puede, pero es más largo (`pvmove` antes de borrar): se hace con calma.

## Paso 3: dejar Debian listo (30 min)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y vulkan-tools mesa-vulkan-drivers git curl
vulkaninfo --summary   # tiene que aparecer la Radeon 860M con RADV
```

## Paso 4: Claude (para que te guíe en lo que sigue)

App de escritorio oficial (comandos de code.claude.com/docs/en/desktop-linux):

```bash
sudo curl -fsSLo /usr/share/keyrings/claude-desktop-archive-keyring.asc https://downloads.claude.ai/claude-desktop/key.asc
gpg --show-keys /usr/share/keyrings/claude-desktop-archive-keyring.asc   # huella 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE
echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/claude-desktop-archive-keyring.asc] https://downloads.claude.ai/claude-desktop/apt/stable stable main" | sudo tee /etc/apt/sources.list.d/claude-desktop.list
sudo apt update && sudo apt install -y claude-desktop
```

Desde ahí, una sesión de Code puede hacer contigo el resto.

## Paso 5: Lightroom Classic

1. Bajar `Lightroom_Classic_on_Linux-v1.2.0-x86_64.AppImage` (303 MB) desde las releases del repositorio, o clonar
   `https://github.com/6im0n/lightroom-classic-on-linux` y usar `./start.sh`.
2. Instalador de Adobe: el de Creative Cloud (la web de Adobe detecta Linux; hay que cambiar el user-agent del
   navegador a Windows) o el `Set-up.exe` de Lightroom Classic. Ver el README del repositorio.
3. Pasos del menú: 1 → 2 → 3/4/5 → 6 → a (máscaras IA) → 7. Si la ventana de límite de equipos sale en blanco
   (Wayland, problema #14), cambiar a X11 con la opción `w`. Linux cuenta como otro equipo para Adobe.
4. Escala de la interfaz: opción `d` (por defecto viene al 150 %).

## Paso 5b: tu plugin IMGorgni en Linux

> **Hecho el 21/11/2026, con cambios respecto de lo que sigue:** el nivelado salió del plugin (Renzo nivela a mano
> con el Automático de Lightroom), así que los pasos 4 y 6 de abajo ya no aplican. Cómo quedó de verdad: sección
> "Resultado del 21/11: Lightroom y Jarvis en Debian".

El plugin ya viene preparado (cambio del 18/11/2026, sin probar todavía en Linux): si detecta Wine, traduce las
rutas `/home/renzo/Renzo/...` que escriban tus scripts y llama al Python de Linux para nivelar. En Windows no cambia
nada. Lo que hay que dejar armado:

1. **Tus carpetas en `~/Renzo` con la misma estructura que en Windows**: al menos
   `~/Renzo/Proyectos/IMGorgni` (con `lightroom/`, `_lightroom/` y las fotos de prueba) y `~/Renzo/Proyectos/Encuadrador`.
2. **El truco que hace funcionar todo**: dentro del disco C: de Wine, un enlace a esa carpeta.
   ```bash
   P=$(dirname "$(find ~ -maxdepth 6 -type d -name drive_c -path '*lightroom*' | head -1)")   # el prefijo de Wine
   ln -s ~/Renzo "$P/drive_c/Renzo"
   ```
   Así, para Lightroom, `C:\Renzo\Proyectos\IMGorgni\_lightroom\cola.tsv` es tu archivo de Linux.
3. **Tus presets**: copiar desde Windows `%APPDATA%\Adobe\CameraRaw\Settings` (los de revelado: "IMGorgni Basica",
   "IMGorgni Denoise 10/20/30") y `%APPDATA%\Adobe\Lightroom` (presets de exportación y marcas de agua) a
   `$P/drive_c/users/<usuario>/AppData/Roaming/Adobe/` dentro del prefijo.
4. **El Python del Encuadrador** (para nivelar):
   ```bash
   cd ~/Renzo/Proyectos/Encuadrador && python3 -m venv .venv && .venv/bin/pip install numpy opencv-python-headless
   ```
5. En Lightroom: Archivo → Administrador de plugins → Añadir → `C:\Renzo\Proyectos\IMGorgni\lightroom\IMGorgni.lrplugin`.
6. Prueba: un trabajo en `cola.tsv` con 5 fotos y `nivelar=si`, y mirar `_lightroom/plugin.log`.

## Paso 6: la prueba (una tarde)

Usar las 110 RAW de la F7 (`Compras/pedidos`), copiadas a Linux, y un catálogo nuevo de prueba.

| Qué probar | Cómo saber si pasa | Referencia en Windows |
|---|---|---|
| GPU detectada | Preferencias → Rendimiento muestra la Radeon 860M con aceleración completa | Sí |
| Denoise IA | Tiempo por foto de 42 MP | ~133 s/foto (prueba Katty, 4/11) |
| Máscaras IA (sujeto, cielo) | Funcionan y cuánto tardan | Sí |
| Borrado generativo | Funciona (es en la nube) | Sí |
| Plugin `IMGorgni.lrplugin` | Aparece en el menú y procesa un trabajo de la cola | Sí |
| Exportar | 50 fotos a 3840 px sin cuelgues | — |
| Histograma | Navegar 200 fotos con el histograma abierto (problema #32) | — |
| Chats de Claude | `claude --resume` muestra tus sesiones copiadas | — |

Si el Denoise, el plugin y la exportación pasan, Linux sirve para trabajar. Si alguno falla, lo anotas y seguimos
en Windows sin haber perdido nada.

## Resultado del 21/11: Lightroom y Jarvis en Debian

### Lightroom Classic + plugin: pasa

Prueba con 5 RAW de la F7 (pedido 5AD44C, edición completa) en un catálogo nuevo de Linux:

| Qué | Resultado |
|---|---|
| Plugin `IMGorgni.lrplugin` | Carga solo y procesa la cola: revelado, modelo de color, ajustes por foto, Denoise, recorte del borde y previas |
| Denoise IA (GPU Radeon 860M) | **~105 s/foto** (Windows: ~62 s en la F7, ~133 s en Katty) |
| Previas JPEG 1600 px | OK |

Cómo quedó armado (detalle en la memoria `linux-es-por-personalizacion`):

- AppImage `6im0n` v1.2.0; prefijo en `~/.local/share/lightroom-classic-on-linux/wineprefix`. Instalado con
  `Lightroom_Classic_Set-Up.exe` (copiado como `resources/installers/Creative_Cloud_Set-Up.exe`) → menú 3, 6 y `a`.
- **Driver X11 forzado** (`wineprefix/.lr-driver-pref`): con Wayland, `wlstack.so` muere por
  `wl_proxy_get_interface` (libwayland 1.23.1 de Debian).
- En el prefijo, enlaces: `drive_c/Renzo` → `~/Renzo`, `drive_c/Fotos` → `/mnt/win/Fotos`, `dosdevices/k:` → `~/K`.
  Así los catálogos de `C:\Fotos\otro\catalogos` abren con las fotos en su lugar.
- Presets **enlazados** (no copiados) a los de Windows: `CameraRaw/Settings` y 17 carpetas de `Adobe/Lightroom`.
- Plugins por enlaces en `AppData/Roaming/Adobe/Lightroom/Modules`: IMGorgni, lr-auto-settings, NegativeLabPro3.
- **Modelos de IA copiados de Windows** (`C:\ProgramData\Adobe\CameraRaw\ModelZoo` y `%APPDATA%\...\ModelZoo`):
  sin eso sale "Some AI models failed to download" y el Denoise se "aplica" sin calcularse.
- `Cola.lua` detecta Wine con `C:\windows\system32\wineboot.exe` (el AppImage desactiva `winemenubuilder`).
- No actualizar Creative Cloud y cerrarlo con `--kill`: su interfaz sin GPU congela el escritorio. "Cerrar sesión"
  sale gris en Wine; para cambiar de cuenta se borra `AppData/Local/Adobe/OOBE/opm.db`, las cookies de
  `AppData/Local/Adobe/webview2` y las credenciales de Adobe en `user.reg`.
- `K:` = `~/K` por sshfs (`~/.local/bin/montar-k`): en la casa por LAN con escritura, fuera por Tailscale y
  solo lectura. Antes, fuera de casa, el sshfs colgado trababa Wine al arrancar.

### Jarvis: la captura corre en Debian

- La Pi ya procesaba la noche (hora, noche, mañana). En Debian se portó solo la **captura**, con agy
  (`Jarvis/docs/spec_agy_jarvis_debian.md`): script de KWin para la ventana activa, `spectacle` para la foto,
  Klipper para el portapapeles e inactividad por Wayland. Servicio `jarvis-captura.service` de usuario.
- Ajustes después de agy: la foto se refresca cada 10 s y no cada 2 (16 % → 5,6 % de un núcleo), y con la **tapa
  cerrada** cuenta como ausente (Antigravity abierto bloquea la inactividad de Wayland).
- Syncthing 2.1.7 con la identidad del Windows (`note-yenllo`): `datos/` sigue sincronizando con la Pi y el teléfono.

### Lo que falta

- Tareas nocturnas de IMGorgni que se usan de verdad: agente calentado, editar Renato, selección IG / feed.

## Lo que igual sigue siendo de Windows

- Juegos con anti-cheat (CoD/Black Ops 6, Apex, Rainbow Six, Destiny 2, Rust).
- IMGorgni tal como está: 83 scripts con rutas `K:\`, 16 lanzadores `.bat`/`.ps1` y tareas programadas. Pasarlo a
  Linux es un trabajo aparte, solo si la prueba sale bien.
- DxO PureRAW (no existe en Linux; RawForge sí).

## Si algo sale mal (volver atrás)

En Windows: Administración de discos → borrar las particiones de Debian → "Extender volumen" en C:. Para sacar
la entrada de Debian del menú de arranque, `bcdedit` o la opción del firmware. Windows nunca se toca.

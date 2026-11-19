# Prueba de Debian 13 en modo live (sin instalar nada)

Pendrive: Imation 8 GB (se llama "FEDORA-KDE-", pero adentro está Debian 13.7 KDE). Nada de esto toca tu disco.
Tiempo: 30–40 minutos. Ten el cargador enchufado.

## 1. Arrancar

1. Apaga el notebook (Apagar, no Reiniciar).
2. Préndelo apretando **F12** (o Fn+F12) varias veces y elige el pendrive (puede salir como "Imation" o "USB HDD").
3. En el menú negro elige **"Live system (amd64)"**.
4. Si sale un error de **Secure Boot**: F2 al prender → pestaña Security → Secure Boot → Disabled. Después lo vuelves
   a activar (Windows funciona igual con o sin él).
5. Entra solo al escritorio. Usuario `user`, clave `live` (por si algo la pide).

**Anota:** ¿arrancó? ¿cuánto tardó?

## 2. Teclado en español (lo primero, si no, no encuentras los símbolos)

Menú (abajo a la izquierda) → **Configuración del sistema** → Teclado → Distribuciones → Añadir →
**Español (latinoamericano)** → Aplicar. Si el sistema está en inglés: System Settings → Keyboard → Layouts →
Add → Spanish (Latin American).

## 3. Wifi (la prueba más importante)

1. Ícono de red abajo a la derecha → conéctate a **Renzo** (o Renzo_5G).
2. Abre Firefox y entra a **fast.com**.

**Anota:** ¿conectó? ¿qué velocidad? (en Windows tu wifi a la Pi da 33–56 MB/s, unos 260–450 Mbps).

## 4. Instalar las herramientas de diagnóstico y sacar el informe

Abre **Konsole** (menú → buscar "Konsole") y pega esto (Ctrl+Shift+V para pegar):

```bash
sudo apt update && sudo apt install -y vulkan-tools mesa-utils
```

Después pega este bloque completo. Junta todo en un archivo y lo muestra en pantalla:

```bash
{
echo "== equipo"; cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name /sys/class/dmi/id/product_version
echo "== kernel"; uname -r; echo "sesion: $XDG_SESSION_TYPE"
echo "== wifi"; lspci -nnk | grep -A3 -i -E "network|wireless"; nmcli -t -f DEVICE,STATE,CONNECTION dev
echo "== gpu"; lspci -nnk | grep -A3 -i -E "vga|display"
echo "== vulkan"; vulkaninfo --summary 2>/dev/null | grep -E "deviceName|driverName|driverInfo|apiVersion"
echo "== opengl"; glxinfo -B 2>/dev/null | grep -E "renderer|version string"
echo "== errores"; sudo dmesg --level=err,warn | grep -i -E "amdgpu|rtw89|firmware|bluetooth|snd|sof" | tail -40
echo "== audio"; wpctl status 2>/dev/null | sed -n '1,30p'
echo "== bateria"; upower -i $(upower -e | grep -i bat) | grep -E "state|percentage|energy-rate|capacity"
echo "== pantalla"; kscreen-doctor -o 2>/dev/null | head -15
echo "== suspension"; cat /sys/power/mem_sleep
echo "== discos"; lsblk -o NAME,SIZE,FSTYPE,LABEL
} > ~/informe_linux.txt 2>&1; cat ~/informe_linux.txt
```

**Lo que tiene que aparecer:**
- En **vulkan**: `AMD Radeon 860M` con `radv`. Si dice `llvmpipe`, la GPU no está andando (el sistema dibuja con el
  procesador).
- En **wifi**: la Realtek 8922AE con `Kernel driver in use: rtw89_8922ae`.
- En **errores**: idealmente vacío o casi. Líneas con `amdgpu` o `rtw89` que digan "failed" son lo que importa.

## 5. Probar a mano (5 minutos)

| Qué | Cómo | Anota |
|---|---|---|
| Fluidez | Mueve ventanas, abre el menú, haz scroll en Firefox | ¿se ve fluido o a saltos? |
| Pantalla | Configuración → Pantalla | resolución, si el texto se ve muy chico (escala) |
| Brillo y volumen | Teclas Fn del teclado | ¿funcionan? |
| Audio | Un video de YouTube | ¿suena? ¿y con audífonos? |
| Touchpad | Scroll con dos dedos, clic | ¿bien? |
| Cámara | webcamtests.com en Firefox | ¿se ve? |
| Bluetooth | Ícono de Bluetooth, buscar un dispositivo | ¿aparece algo? |
| Suspensión | Cierra la tapa 30 s y ábrela | ¿volvió con la wifi? |

## 6. Tu disco de fotos (la Pi)

En Dolphin (el explorador de archivos), escribe en la barra de dirección: `smb://192.168.1.62/Fotos`
(pide el mismo usuario y clave con que conectas K: en Windows). Abre una foto JPG. Si quieres medir velocidad, copia
un RAW (~43 MB) al Escritorio.

**No abras la partición de Windows** (aparece en Dolphin como un disco de ~950 GB): tu Windows tiene el inicio rápido
activado y abrirla desde Linux puede dañarla.

## 7. (Opcional) La app de Claude

Si quieres probarla en caliente (se borra al apagar, el live no guarda nada), pega en Konsole los 5 comandos del
Paso 4 de `plan_prueba_linux.md`, abre "Claude" desde el menú y entra a tu cuenta.

## 8. Guardar el informe para que yo lo revise

En Dolphin: copia `informe_linux.txt` (está en tu carpeta personal) a `smb://192.168.1.62/Fotos/_linux_prueba/`
(crea la carpeta si no existe). Yo lo leo desde K:. Si no alcanzas, una foto con el teléfono de la pantalla del
informe también sirve.

Para salir: menú → Apagar, saca el pendrive cuando se apague, y el notebook vuelve a Windows como siempre.

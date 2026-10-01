#!/bin/bash
# Deja el 8TB listo para desenchufar.
#
# No alcanza con desmontar: /mnt/fotos tiene x-systemd.automount, asi que
# cualquier cosa que toque la carpeta lo vuelve a montar sola. Hay que parar
# primero lo que lo usa y desarmar el disparador.
#
# Arrancar el disco montado deja el NTFS sucio y despues no monta hasta correr
# chkdsk desde Windows. Eso ya paso una vez, el 12/8/2026.
set -u

if [ "$(id -u)" -ne 0 ]; then
    echo "Corrantelo con sudo: sudo expulsar-disco"
    exit 1
fi

if ! findmnt -no TARGET /mnt/fotos >/dev/null 2>&1; then
    echo "El disco ya estaba desmontado. Podes desenchufarlo."
    systemctl stop mnt-fotos.automount 2>/dev/null
    exit 0
fi

echo "Parando lo que usa el disco..."
systemctl stop galeria 2>/dev/null
systemctl stop smbd 2>/dev/null

echo "Desarmando el automount y desmontando..."
systemctl stop mnt-fotos.automount mnt-fotos.mount 2>/dev/null
sync
sleep 1

# La verificacion es el punto del script: si sigue montado hay que decirlo
# fuerte, no dar un OK optimista.
if findmnt -no TARGET /mnt/fotos >/dev/null 2>&1; then
    echo
    echo "=============================================="
    echo "  NO se desmonto. NO DESENCHUFES EL DISCO."
    echo "=============================================="
    echo
    echo "Algo lo tiene ocupado:"
    fuser -vm /mnt/fotos 2>&1 | head -20
    echo
    echo "Desde Windows, soltá la unidad:  net use K: /delete"
    echo "Despues volve a correr este script."
    systemctl start smbd galeria 2>/dev/null
    exit 1
fi

echo
echo "=============================================="
echo "  LISTO. Ya podes desenchufar el disco."
echo "=============================================="
echo
echo "Cuando vuelva a estar enchufado, corre:  sudo montar-disco"

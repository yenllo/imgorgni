#!/bin/bash
# Devuelve el 8TB al servicio despues de haberlo expulsado.
set -u

if [ "$(id -u)" -ne 0 ]; then
    echo "Corrantelo con sudo: sudo montar-disco"
    exit 1
fi

if [ ! -b /dev/disk/by-uuid/60D260D2D260ADCA ]; then
    echo "El disco NO esta conectado (no aparece su UUID)."
    echo "Enchufalo, esperá que arranque, y volve a correr esto."
    exit 1
fi

echo "Armando el automount..."
systemctl reset-failed mnt-fotos.mount 2>/dev/null
systemctl start mnt-fotos.automount
ls /mnt/fotos >/dev/null 2>&1
sleep 2

if ! findmnt -no SOURCE /mnt/fotos >/dev/null 2>&1; then
    echo
    echo "NO monto. Lo mas probable es el dirty bit del NTFS:"
    journalctl -k --no-pager 2>/dev/null | grep -i ntfs3 | tail -3
    echo
    echo "Si dice 'volume is dirty', conecta el disco a la notebook y corre"
    echo "en PowerShell como administrador:"
    echo "    Repair-Volume -DriveLetter K -OfflineScanAndFix"
    exit 1
fi

echo "Montado: $(findmnt -no SOURCE,FSTYPE /mnt/fotos)  $(df -h --output=size,used,avail /mnt/fotos | tail -1)"

echo "Levantando servicios..."
systemctl start smbd galeria 2>/dev/null
sleep 1
for s in smbd galeria caddy; do
    printf "  %-9s %s\n" "$s" "$(systemctl is-active $s)"
done

echo
echo "LISTO."

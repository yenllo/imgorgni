#!/usr/bin/env bash
# ingesta — copia sola una tarjeta de camara al 8TB cuando la enchufas a la Pi.
#
# Existe para sacar a la notebook del camino. Antes una fecha entraba
# notebook -> Pi (wifi, 33-56 MB/s) o desenchufando el disco y llevandolo; asi
# entra tarjeta -> Pi -> 8TB, todo dentro de la misma maquina y por USB3.
#
# Reglas que no se negocian:
#   - NUNCA borra nada de la tarjeta. La tarjeta es la otra copia hasta que
#     Renzo decida formatearla en la camara.
#   - NUNCA escribe en el SSD del sistema. Es de 240 GB y esta en un puerto
#     USB2: no entra una fecha y ademas seria lento. Si el 8TB no esta
#     montado, la ingesta no arranca.
#   - La tarjeta se monta de solo lectura.
#   - Las tarjetas se hacen COLA, no se pisan. Renzo usa tres por fecha y con
#     un candado que rebota se perderia la segunda y la tercera en silencio.
#
# Uso:
#   ingesta <kname>    lo llama udev cuando aparece una particion USB
#   ingesta estado     que paso en la ultima corrida
#   ingesta probar     self-check contra una tarjeta de mentira
set -uo pipefail

FOTOS=/mnt/fotos                     # el 8TB
DESTINO_RAIZ="$FOTOS/ingesta"
PUNTO=/run/ingesta/tarjeta
ESTADO=/run/ingesta/estado
CANDADO=/run/ingesta/candado
# el 8TB, para no ingerirlo a si mismo si algun dia se reenchufa el dock
UUID_FOTOS=60D260D2D260ADCA

# A que hora empieza un dia nuevo, para nombrar la carpeta. Un partido de
# noche arranca 21:30 y termina 01:00: con el corte a medianoche las tarjetas
# de la misma fecha caerian en dos carpetas distintas segun cuando se lleno
# cada una. Con el corte a las 6 de la manana, todas caen en el dia que Renzo
# llama "la fecha".
CORTE_HORAS=6

log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; logger -t ingesta -- "$*"; }

anotar() {   # anotar clave valor  -> deja el estado legible con `ingesta estado`
    mkdir -p "$(dirname "$ESTADO")"
    grep -v "^$1=" "$ESTADO" 2>/dev/null > "$ESTADO.tmp"
    printf '%s=%s\n' "$1" "$2" >> "$ESTADO.tmp"
    mv "$ESTADO.tmp" "$ESTADO"
}

# --------------------------------------------------------------- el copiado
# Separado del manejo del dispositivo para poder probarlo sin una tarjeta real.
# origen = la carpeta DCIM ya montada, destino_raiz = donde van las fechas.
copiar() {
    local dcim="$1" destino_raiz="$2" etiqueta="${3:--}" fecha destino epoca

    # La fecha del evento sale de la foto MAS VIEJA de la tarjeta, no de hoy:
    # es la diferencia entre "2026-09-03" y "2026-09-07" cuando la tarjeta
    # aparece cuatro dias despues, que es exactamente lo que suele pasar.
    # Y se le restan CORTE_HORAS antes de mirar el dia, para que lo que se
    # tomo despues de medianoche cuente como la noche anterior.
    epoca=$(find "$dcim" -type f \
                 \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.arw' \
                    -o -iname '*.raw' -o -iname '*.dng' -o -iname '*.heif' \) \
                 -printf '%T@\n' 2>/dev/null | cut -d. -f1 | sort -n | head -1)
    if [ -z "$epoca" ]; then
        log "la tarjeta no tiene fotos que reconozca; no copio nada"
        return 3
    fi
    fecha=$(date -d "@$((epoca - CORTE_HORAS * 3600))" '+%F')
    destino="$destino_raiz/$fecha"

    local n bytes
    n=$(find "$dcim" -type f | wc -l)
    bytes=$(du -sb "$dcim" | cut -f1)
    log "tarjeta del $fecha: $n archivos, $((bytes / 1048576)) MB -> $destino"
    anotar estado copiando
    anotar fecha "$fecha"
    anotar destino "$destino"
    anotar archivos "$n"
    anotar mb "$((bytes / 1048576))"
    anotar desde "$(date '+%F %T')"

    mkdir -p "$destino" || return 1

    # Si dos tarjetas traen el mismo nombre de archivo con contenido distinto
    # —la camara puesta en "reiniciar numeracion" en vez de "serie"— el que
    # llega segundo pisaria al primero y la foto se perderia sin ruido.
    # --backup-dir guarda el viejo antes de escribir encima: nada se pierde,
    # y la carpeta queda como aviso de que hay que revisar la camara.
    # Vive FUERA de la carpeta de la fecha a proposito: el escaneo de galerias
    # es recursivo y ahi adentro las entraria como fotos repetidas.
    local respaldo="$destino_raiz/_colisiones/$fecha/$(date '+%H%M%S')"

    # -a preserva fechas y estructura; rsync verifica el checksum de cada
    # archivo al terminar de transferirlo, asi que no hace falta una segunda
    # pasada. Sin --delete: la tarjeta manda, el destino nunca se poda.
    rsync -a --partial --info=stats2 \
          --backup --backup-dir="$respaldo" "$dcim/" "$destino/" || return 1
    sync

    # una linea por tarjeta: es como se sabe que entraron las tres
    printf '%s\t%s\t%s archivos\t%s MB\n' \
        "$(date '+%F %T')" "$etiqueta" "$n" "$((bytes / 1048576))" \
        >> "$destino/_ingesta.log"
    anotar tarjetas "$(wc -l < "$destino/_ingesta.log")"

    if [ -d "$respaldo" ] && [ -n "$(ls -A "$respaldo" 2>/dev/null)" ]; then
        log "OJO: $(find "$respaldo" -type f | wc -l) archivos tenian el mismo"\
            "nombre que otros ya copiados. Los viejos quedaron en $respaldo."\
            "Revisa que la camara este en numeracion 'serie', no 'reiniciar'."
        anotar colisiones "$respaldo"
    fi
    return 0
}

# ------------------------------------------------------------ el dispositivo
principal() {
    local k="$1" dev="/dev/$1" padre raiz uuid

    [ -b "$dev" ] || { log "$dev no es un dispositivo de bloque"; return 1; }

    uuid=$(lsblk -no UUID "$dev" 2>/dev/null)
    if [ "$uuid" = "$UUID_FOTOS" ]; then
        log "$k es el 8TB, no una tarjeta"; return 0
    fi
    padre=$(lsblk -no PKNAME "$dev" 2>/dev/null)
    raiz=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)" 2>/dev/null)
    if [ -n "$padre" ] && [ "$padre" = "$raiz" ]; then
        log "$k vive en el disco del sistema, no lo toco"; return 0
    fi

    # el 8TB tiene que estar montado ANTES de copiar: si no, el destino caeria
    # en el SSD del sistema, que no tiene lugar
    if ! mountpoint -q "$FOTOS"; then
        ls "$FOTOS" >/dev/null 2>&1     # despierta el automount de fstab
    fi
    if ! mountpoint -q "$FOTOS"; then
        log "ERROR: $FOTOS no esta montado (el 8TB no esta enchufado). No copio."
        anotar estado sin_disco
        return 1
    fi

    mkdir -p "$PUNTO"
    umount "$PUNTO" 2>/dev/null
    if ! mount -o ro "$dev" "$PUNTO" 2>/dev/null; then
        log "no pude montar $dev de solo lectura"; return 1
    fi
    # trap para que la tarjeta quede desmontada pase lo que pase: si queda
    # montada, sacarla a lo bruto es justo como se corrompe una tarjeta
    trap 'umount "$PUNTO" 2>/dev/null' EXIT

    local dcim
    dcim=$(find "$PUNTO" -maxdepth 2 -iname DCIM -type d | head -1)
    if [ -z "$dcim" ]; then
        log "$k no tiene carpeta DCIM: no parece una tarjeta de camara"
        return 0
    fi

    local etiqueta
    etiqueta=$(lsblk -no LABEL "$dev" 2>/dev/null | tr -d ' ')
    [ -z "$etiqueta" ] && etiqueta="$uuid"
    [ -z "$etiqueta" ] && etiqueta="$k"
    anotar tarjeta "$etiqueta"

    local r=0
    copiar "$dcim" "$DESTINO_RAIZ" "$etiqueta" || r=$?
    umount "$PUNTO" 2>/dev/null
    trap - EXIT
    anotar hasta "$(date '+%F %T')"
    if [ "$r" -eq 0 ]; then
        anotar estado listo
        log "LISTO. La tarjeta quedo desmontada, ya se puede sacar."
    else
        anotar estado error
        log "ERROR en la copia (codigo $r). La tarjeta NO se toco."
    fi
    return "$r"
}

# ------------------------------------------------------------------ el check
probar() {
    local t; t=$(mktemp -d)
    ESTADO="$t/estado"
    local tarjeta="$t/tarjeta/DCIM/100MSDCF" dest="$t/destino"
    mkdir -p "$tarjeta"
    head -c 2048 /dev/urandom > "$tarjeta/DSC00001.JPG"
    head -c 2048 /dev/urandom > "$tarjeta/DSC00002.ARW"
    touch -d '2026-09-03 21:14' "$tarjeta/DSC00001.JPG"
    touch -d '2026-09-04 01:02' "$tarjeta/DSC00002.ARW"

    copiar "$t/tarjeta/DCIM" "$dest" >/dev/null || {
        echo "FALLO: la copia devolvio error"; return 1; }

    # la carpeta se llama como la foto mas vieja, no como hoy
    [ -d "$dest/2026-09-03" ] || {
        echo "FALLO: esperaba $dest/2026-09-03, hay: $(ls "$dest")"; return 1; }
    # llegaron las dos, con la estructura de la camara
    [ -f "$dest/2026-09-03/100MSDCF/DSC00001.JPG" ] \
        && [ -f "$dest/2026-09-03/100MSDCF/DSC00002.ARW" ] || {
        echo "FALLO: falta alguna foto en el destino"; return 1; }
    cmp -s "$tarjeta/DSC00001.JPG" "$dest/2026-09-03/100MSDCF/DSC00001.JPG" || {
        echo "FALLO: el archivo copiado no es igual al original"; return 1; }
    # y la tarjeta quedo intacta: esta es la regla que no se negocia
    [ "$(find "$t/tarjeta" -type f | wc -l)" -eq 2 ] || {
        echo "FALLO: la ingesta toco la tarjeta"; return 1; }

    # correrla de nuevo no duplica ni rompe nada
    copiar "$t/tarjeta/DCIM" "$dest" >/dev/null || {
        echo "FALLO: la segunda corrida dio error"; return 1; }
    # solo las fotos: _ingesta.log tambien vive ahi y no es una copia
    [ "$(find "$dest" -type f \( -name '*.JPG' -o -name '*.ARW' \) | wc -l)" -eq 2 ] || {
        echo "FALLO: la segunda corrida duplico archivos"; return 1; }

    # una tarjeta sin fotos no inventa una carpeta
    mkdir -p "$t/vacia/DCIM"
    copiar "$t/vacia/DCIM" "$dest" >/dev/null 2>&1
    [ "$?" -eq 3 ] || { echo "FALLO: una tarjeta vacia no aviso"; return 1; }

    # ---- tres tarjetas de la misma fecha, con el partido cruzando medianoche
    local d2="$t/dest2" i
    for i in 1 2 3; do
        mkdir -p "$t/card$i/DCIM/10${i}MSDCF"
        head -c 512 /dev/urandom > "$t/card$i/DCIM/10${i}MSDCF/DSC0000$i.JPG"
    done
    # la 1 antes de medianoche, la 2 justo despues, la 3 de madrugada
    touch -d '2026-09-03 21:30' "$t/card1/DCIM/101MSDCF/DSC00001.JPG"
    touch -d '2026-09-04 00:20' "$t/card2/DCIM/102MSDCF/DSC00002.JPG"
    touch -d '2026-09-04 01:45' "$t/card3/DCIM/103MSDCF/DSC00003.JPG"
    for i in 1 2 3; do
        copiar "$t/card$i/DCIM" "$d2" "tarjeta$i" >/dev/null || {
            echo "FALLO: la tarjeta $i no copio"; return 1; }
    done
    # las tres en UNA carpeta, la de la noche que empezo el partido
    [ "$(ls "$d2")" = "2026-09-03" ] || {
        echo "FALLO: las 3 tarjetas se partieron en: $(ls "$d2" | tr '\n' ' ')"
        return 1; }
    [ "$(find "$d2/2026-09-03" -name '*.JPG' | wc -l)" -eq 3 ] || {
        echo "FALLO: no llegaron las 3 fotos"; return 1; }
    # y el registro dice que entraron las tres, con su nombre
    [ "$(wc -l < "$d2/2026-09-03/_ingesta.log")" -eq 3 ] || {
        echo "FALLO: el log no anoto las 3 tarjetas"; return 1; }
    grep -q tarjeta3 "$d2/2026-09-03/_ingesta.log" || {
        echo "FALLO: el log no dice cual tarjeta fue"; return 1; }

    # ---- dos tarjetas con el MISMO nombre y distinto contenido
    # ponytail: rsync decide por tamano+fecha. Dos fotos distintas que
    # coincidieran en los dos no se copiarian y el respaldo no se enteraria.
    # Entre dos JPEG de verdad eso no pasa; cerrarlo pediria --checksum, que
    # son 300 GB leidos dos veces por cada fecha.
    local d3="$t/dest3"
    mkdir -p "$t/choqueA/DCIM/100MSDCF" "$t/choqueB/DCIM/100MSDCF"
    echo "la primera" > "$t/choqueA/DCIM/100MSDCF/DSC00001.JPG"
    echo "la segunda, que pesa distinto" > "$t/choqueB/DCIM/100MSDCF/DSC00001.JPG"
    touch -d '2026-09-03 20:00' "$t/choqueA/DCIM/100MSDCF/DSC00001.JPG"
    touch -d '2026-09-03 20:05' "$t/choqueB/DCIM/100MSDCF/DSC00001.JPG"
    copiar "$t/choqueA/DCIM" "$d3" A >/dev/null || return 1
    copiar "$t/choqueB/DCIM" "$d3" B >/dev/null || return 1
    # la segunda quedo puesta...
    grep -q "la segunda" "$d3/2026-09-03/100MSDCF/DSC00001.JPG" || {
        echo "FALLO: la segunda tarjeta no se copio"; return 1; }
    # ...y la primera NO se perdio: esta respaldada, y fuera de la fecha
    grep -rqs "la primera" "$d3/_colisiones/" || {
        echo "FALLO: la foto pisada se perdio"; return 1; }
    [ -z "$(find "$d3/2026-09-03" -path '*_colisiones*' -print -quit)" ] || {
        echo "FALLO: el respaldo quedo adentro de la fecha"; return 1; }

    rm -rf "$t"
    echo "ok"
}

case "${1:-}" in
    "")       echo "uso: ingesta <kname> | estado | probar" >&2; exit 2 ;;
    estado)   cat "$ESTADO" 2>/dev/null || echo "todavia no corrio ninguna" ;;
    probar)   probar ;;
    *)        # /run es tmpfs: despues de cada reinicio no existe, y sin esto
              # el `exec 9>` de abajo mata el guion antes de copiar nada
              mkdir -p /run/ingesta || {
                  log "ERROR: no pude crear /run/ingesta"; exit 1; }
              # cada fallo del candado tiene que sonar distinto. Si los tres
              # dijeran "ya hay una corriendo" y salieran con 0, un permiso
              # mal puesto se veria igual que el caso normal y la tarjeta no
              # se copiaria nunca, en silencio.
              exec 9>"$CANDADO" || {
                  log "ERROR: no pude abrir el candado $CANDADO"; exit 1; }
              # ESPERA, no rebota. Renzo mete tres tarjetas por fecha y si la
              # segunda se topa con la primera copiando y se va, esa tarjeta no
              # se copia nunca y nadie se entera. Que hagan cola cuesta una
              # espera; rebotar cuesta una tarjeta.
              flock -w 21600 9 || {
                  log "ERROR: 6 horas esperando el candado, me rindo"; exit 1; }
              principal "$1" ;;
esac

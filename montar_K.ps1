# Mapea K: al NAS de la Pi. Prueba LAN primero (gigabit), cae a Tailscale si no esta.
# Uso: powershell -File montar_K.ps1

# IP fija por reserva DHCP (lease Infinity, MAC e4:5f:01:0a:51:27, puerto LAN2 del router).
# Va por IP y no por hostname a proposito: la reserva es por MAC, asi que sobrevive a
# regrabar el SSD y no depende de que mDNS resuelva.
$LAN = '192.168.1.62'
$TS  = '100.78.164.81'          # <-- IP Tailscale de la Pi (tailscale ip -4)

# ponytail: probamos el puerto SMB en vez de ping; muchos routers bloquean ICMP.
# TcpClient en vez de Test-NetConnection porque ese no acepta timeout y tarda
# ~30 s en rendirse: fuera de casa la IP de la LAN no existe y hay que esperarlo
# entero antes de que caiga a Tailscale. 1,5 s alcanza de sobra en una LAN.
function Alcanzable($h, $ms = 1500) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { return ($c.ConnectAsync($h, 445).Wait($ms) -and $c.Connected) }
    catch { return $false }
    finally { $c.Close() }
}

# La Pi anuncia 192.168.1.0/24 como ruta de subred en Tailscale, asi que la IP
# de la LAN CONTESTA desde cualquier lado — pero por Tailscale y con un salto
# de mas (medido 21/8/2026: 41 ms contra 29 ms yendo directo). Por eso no
# alcanza con que responda: hay que confirmar que esta maquina esta de verdad
# en esa red, mirando sus propias IPs.
$enLaRed = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Where-Object { $_.IPAddress -like '192.168.1.*' }).Count -gt 0

$nas = if ($enLaRed -and (Alcanzable $LAN)) { $LAN }
       elseif (Alcanzable $TS) { $TS }
       elseif (Alcanzable $LAN) { $LAN }   # ultimo recurso: anda, aunque de la vuelta
       else { throw "NAS inalcanzable por LAN ($LAN) y por Tailscale ($TS). Prendida? Tailscale corriendo?" }

net use K: /delete /y | Out-Null        # si no existe, no pasa nada
net use K: "\\$nas\Fotos" /user:yenllo /persistent:yes

# El puerto del modem de la casa nueva es de 100 Mbps, no gigabit (verificado
# con ethtool el 21/8/2026): el techo real por LAN es ~11 MB/s, no 110.
$via = if ($nas -eq $LAN -and $enLaRed) { 'LAN ~11 MB/s (puerto de 100 del modem)' }
       elseif ($nas -eq $LAN) { 'LAN por ruta de Tailscale — mas lento que ir directo' }
       else { 'Tailscale' }
Write-Host "K: -> \\$nas\Fotos  ($via)"
Write-Host "Verificar con: python verificar_nas.py"

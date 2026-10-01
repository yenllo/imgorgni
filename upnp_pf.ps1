# Port forwarding por UPnP en el Zhone ZNID-GPON-2426A1-1GT de GTD.
# La UI web del router (puerto 80) esta cerrada por el ISP; el demonio UPnP en :5431 no.
#
#   .\upnp_pf.ps1                                                     # listar
#   .\upnp_pf.ps1 add -Ext 443 -Client 192.168.1.62 -Int 443 -Desc pi # crear
#   .\upnp_pf.ps1 del -Ext 443                                        # borrar
#   .\upnp_pf.ps1 test                                                # self-check
#
# ponytail: el control URL lleva un UUID que el router regenera al reiniciar,
# por eso se descubre por SSDP en cada corrida en vez de hardcodearlo.

param(
  [ValidateSet('list','add','del','test','ip')] [string]$Action = 'list',
  [ValidateSet('TCP','UDP')] [string]$Proto = 'TCP',
  [int]$Ext,
  [string]$Client,
  [int]$Int,
  [string]$Desc = 'manual',
  [int]$Lease = 0,          # 0 = indefinido
  [string]$Gateway
)

$ErrorActionPreference = 'Stop'

function Get-Gateway {
  if ($Gateway) { return $Gateway }
  $c = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | Select-Object -First 1
  if (-not $c) { throw "sin gateway por defecto" }
  return $c.IPv4DefaultGateway.NextHop
}

function Get-ControlUrl($gw) {
  $u = New-Object System.Net.Sockets.UdpClient(0)
  try {
    $m = "M-SEARCH * HTTP/1.1`r`nHOST: ${gw}:1900`r`nMAN: `"ssdp:discover`"`r`nMX: 1`r`n" +
         "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1`r`n`r`n"
    $b = [Text.Encoding]::ASCII.GetBytes($m)
    [void]$u.Send($b, $b.Length, $gw, 1900)
    $remote = New-Object Net.IPEndPoint([Net.IPAddress]::Any, 0)
    $deadline = (Get-Date).AddSeconds(5)
    $loc = $null
    while ((Get-Date) -lt $deadline -and -not $loc) {
      if ($u.Available -gt 0) {
        $txt = [Text.Encoding]::ASCII.GetString($u.Receive([ref]$remote))
        $loc = ($txt -split "`r`n" | Where-Object { $_ -match '^(?i)location:' }) -replace '(?i)^location:\s*', ''
      } else { Start-Sleep -Milliseconds 200 }
    }
  } finally { $u.Close() }
  if (-not $loc) { throw "el router no respondio SSDP en $gw (UPnP apagado?)" }

  $body = (Invoke-WebRequest -Uri $loc -TimeoutSec 8 -UseBasicParsing).Content
  if ($body -is [byte[]]) { $body = [Text.Encoding]::UTF8.GetString($body) }
  $xml = [xml]$body

  # WANPPPConnection en enlaces PPPoE, WANIPConnection en IPoE. Tomamos el que haya.
  $svc = $xml.SelectNodes('//*[local-name()="service"]') |
         Where-Object { $_.serviceType -match 'WAN(IP|PPP)Connection:1$' } |
         Select-Object -First 1
  if (-not $svc) { throw "el router no expone WANIPConnection/WANPPPConnection" }

  $base = [Uri]$loc
  return @{
    Url  = (New-Object Uri($base, $svc.controlURL)).AbsoluteUri
    Type = $svc.serviceType
  }
}

function Invoke-Soap($ctl, $action, $body) {
  $soap = '<?xml version="1.0"?><s:Envelope ' +
          'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" ' +
          's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:' +
          $action + ' xmlns:u="' + $ctl.Type + '">' + $body + '</u:' + $action + '></s:Body></s:Envelope>'
  try {
    $r = Invoke-WebRequest -Uri $ctl.Url -Method Post -Body $soap `
           -ContentType 'text/xml; charset="utf-8"' `
           -Headers @{ 'SOAPAction' = '"' + $ctl.Type + '#' + $action + '"' } `
           -TimeoutSec 10 -UseBasicParsing
    $c = $r.Content
    if ($c -is [byte[]]) { $c = [Text.Encoding]::UTF8.GetString($c) }
    return @{ ok = $true; xml = [xml]$c }
  } catch {
    $t = ''
    if ($_.Exception.Response) {
      $t = (New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())).ReadToEnd()
    }
    $err = ''
    if ($t -match '<errorDescription>(.*?)</errorDescription>') { $err = $matches[1] }
    if ($t -match '<errorCode>(.*?)</errorCode>')               { $err = "$($matches[1]) $err" }
    if (-not $err) { $err = $_.Exception.Message }
    return @{ ok = $false; error = $err }
  }
}

function Get-Val($xml, $name) {
  $n = $xml.SelectSingleNode("//New$name")
  if ($n) { return $n.InnerText } else { return '' }
}

function Show-List($ctl) {
  $wan = Invoke-Soap $ctl 'GetExternalIPAddress' ''
  if ($wan.ok) { "IP WAN: $(Get-Val $wan.xml 'ExternalIPAddress')`n" }
  $i = 0
  while ($true) {
    $m = Invoke-Soap $ctl 'GetGenericPortMappingEntry' "<NewPortMappingIndex>$i</NewPortMappingIndex>"
    if (-not $m.ok) { break }
    "{0,-3} {1,-4} ext:{2,-6} -> {3,-15}:{4,-6} on:{5} '{6}'" -f $i,
      (Get-Val $m.xml 'Protocol'), (Get-Val $m.xml 'ExternalPort'),
      (Get-Val $m.xml 'InternalClient'), (Get-Val $m.xml 'InternalPort'),
      (Get-Val $m.xml 'Enabled'), (Get-Val $m.xml 'PortMappingDescription')
    $i++
  }
  "`ntotal: $i"
}

function Add-Map($ctl, $proto, $ext, $client, $int, $desc, $lease) {
  Invoke-Soap $ctl 'AddPortMapping' (
    "<NewRemoteHost></NewRemoteHost><NewExternalPort>$ext</NewExternalPort>" +
    "<NewProtocol>$proto</NewProtocol><NewInternalPort>$int</NewInternalPort>" +
    "<NewInternalClient>$client</NewInternalClient><NewEnabled>1</NewEnabled>" +
    "<NewPortMappingDescription>$desc</NewPortMappingDescription>" +
    "<NewLeaseDuration>$lease</NewLeaseDuration>")
}

function Remove-Map($ctl, $proto, $ext) {
  Invoke-Soap $ctl 'DeletePortMapping' (
    "<NewRemoteHost></NewRemoteHost><NewExternalPort>$ext</NewExternalPort>" +
    "<NewProtocol>$proto</NewProtocol>")
}

function Get-Map($ctl, $proto, $ext) {
  Invoke-Soap $ctl 'GetSpecificPortMappingEntry' (
    "<NewRemoteHost></NewRemoteHost><NewExternalPort>$ext</NewExternalPort>" +
    "<NewProtocol>$proto</NewProtocol>")
}

$gw  = Get-Gateway
$ctl = Get-ControlUrl $gw

switch ($Action) {

  'list' { Show-List $ctl }

  'ip' {
    # Detector de CGNAT: si la IP que el router cree tener en su WAN no es la
    # misma que ve internet, hay otro NAT arriba y NINGUN port forward puede
    # funcionar, por bien configurado que este.
    $r = Invoke-Soap $ctl 'GetExternalIPAddress' ''
    $wan = if ($r.ok) { Get-Val $r.xml 'NewExternalIPAddress' } else { "(sin respuesta: $($r.error))" }
    $real = try { (Invoke-RestMethod -Uri 'https://api.ipify.org?format=json' -TimeoutSec 10).ip }
            catch { '(no se pudo consultar)' }
    "WAN segun el router : $wan"
    "Vista desde internet: $real"
    if ($wan -and $real -and $wan -ne $real) {
      ""
      "=> DISTINTAS: hay CGNAT. El port forwarding no puede funcionar desde acá."
    } elseif ($wan -eq $real) {
      ""
      "=> IGUALES: sin CGNAT, el port forwarding deberia funcionar."
    }
  }

  'add' {
    if (-not $Ext -or -not $Client) { throw "faltan -Ext y -Client" }
    if (-not $Int) { $Int = $Ext }
    $prev = Get-Map $ctl $Proto $Ext
    if ($prev.ok) {
      $who = Get-Val $prev.xml 'InternalClient'
      if ($who -ne $Client) { Write-Warning "ext:$Ext/$Proto ya apuntaba a $who, se pisa con $Client" }
    }
    $r = Add-Map $ctl $Proto $Ext $Client $Int $Desc $Lease
    if ($r.ok) { "OK  ext:$Ext/$Proto -> ${Client}:$Int  ($Desc)" }
    else       { "FALLO ext:$Ext/$Proto -> $($r.error)" }
  }

  'del' {
    if (-not $Ext) { throw "falta -Ext" }
    $r = Remove-Map $ctl $Proto $Ext
    if ($r.ok) { "BORRADO ext:$Ext/$Proto" } else { "FALLO -> $($r.error)" }
  }

  'test' {
    # Round trip completo en un puerto muerto: crear, verificar, borrar, verificar.
    $p = 51997
    $me = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } |
           Select-Object -First 1).IPv4Address.IPAddress

    $r = Add-Map $ctl 'TCP' $p $me $p 'upnp-pf-selftest' 0
    if (-not $r.ok) { throw "TEST FALLO: no pudo crear -> $($r.error)" }

    $q = Get-Map $ctl 'TCP' $p
    if (-not $q.ok) { throw "TEST FALLO: creado pero no aparece" }
    $got = Get-Val $q.xml 'InternalClient'
    if ($got -ne $me) { throw "TEST FALLO: apunta a $got en vez de $me" }

    $d = Remove-Map $ctl 'TCP' $p
    if (-not $d.ok) { throw "TEST FALLO: no pudo borrar -> $($d.error)  (limpiar a mano: del -Ext $p)" }
    if ((Get-Map $ctl 'TCP' $p).ok) { throw "TEST FALLO: sigue existiendo despues del borrado" }

    "TEST OK  (gateway $gw, $($ctl.Type))"
  }
}

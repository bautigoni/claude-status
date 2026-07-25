# Voz del bichito. La invoca bichito-hook.exe con el JSON del hook por stdin.
#
# El texto ya viene resuelto desde el hook, en el campo bichito_texto: se elige
# la plantilla segun el estado (termino / esperando) y se reemplaza {proyecto}.
#
# Notas de implementacion (heredadas de speak-waiting.ps1, que funcionaba bien):
#  - Voz principal: edge-tts (neuronal, es-AR-TomasNeural). Requiere internet.
#  - Cachea el mp3: la primera vez de cada frase sale a la red, despues suena
#    instantaneo y offline.
#  - La clave del cache sale del HASH DEL TEXTO, no del proyecto: los mensajes
#    son configurables desde el panel, y con una clave por proyecto el primer
#    cambio seguiria reproduciendo el audio viejo para siempre.
#  - Fallback si edge-tts falla o no hay internet: voz local via WinRT.
#    Nunca queda mudo.
#  - Los acentos NUNCA viajan por linea de comandos (se rompen por codepage):
#    llegan por stdin en JSON y se pasan a edge-tts via archivo UTF-8 con --file.

$ErrorActionPreference = 'Stop'

$texto = ''
$proyecto = ''
try {
    $payload = [Console]::In.ReadToEnd()
    if ($payload) {
        $j = ConvertFrom-Json $payload
        $texto = $j.bichito_texto
        if ($j.cwd) { $proyecto = Split-Path -Leaf $j.cwd }
    }
} catch { }

if (-not $texto) {
    # por si se invoca a mano o el hook no resolvio la plantilla
    $a = [char]0xE1
    if (-not $proyecto -and $env:CLAUDE_PROJECT_DIR) {
        $proyecto = Split-Path -Leaf $env:CLAUDE_PROJECT_DIR
    }
    if ($proyecto) { $texto = "El proyecto $proyecto est$a esperando tu respuesta" }
    else           { $texto = "Claude est$a esperando tu respuesta" }
}

$voz = 'es-AR-TomasNeural'

$cacheDir = Join-Path $env:TEMP 'claude-voz-cache'
if (-not (Test-Path $cacheDir)) { New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null }

# clave = hash del texto exacto, asi cambiar el mensaje invalida el cache solo
$sha = [System.Security.Cryptography.SHA1]::Create()
$bytes = [System.Text.Encoding]::UTF8.GetBytes($texto)
$clave = ([System.BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').Substring(0, 12)
$mp3 = Join-Path $cacheDir "$voz-$clave.mp3"

function Invoke-EdgeTts($texto, $voz, $destino) {
    # El texto va por archivo UTF-8, no por argv, para no perder los acentos.
    $txt = Join-Path $env:TEMP ("claude-voz-" + $PID + ".txt")
    [System.IO.File]::WriteAllText($txt, $texto, (New-Object System.Text.UTF8Encoding $false))
    try {
        $tmp = "$destino.tmp"
        & python -m edge_tts --voice $voz --file $txt --write-media $tmp 2>$null
        if ($LASTEXITCODE -ne 0) { throw "edge-tts salio con $LASTEXITCODE" }
        if (-not (Test-Path $tmp) -or (Get-Item $tmp).Length -lt 1024) { throw "mp3 vacio" }
        Move-Item $tmp $destino -Force   # escritura atomica: nunca queda un mp3 a medias
    } finally {
        Remove-Item $txt -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-PlayMp3($ruta) {
    Add-Type -AssemblyName PresentationCore
    $p = New-Object System.Windows.Media.MediaPlayer
    $p.Open([uri]$ruta)
    # Open() es async: esperar a que cargue la duracion antes de reproducir.
    $limite = (Get-Date).AddSeconds(5)
    while (-not $p.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $limite) {
        Start-Sleep -Milliseconds 50
    }
    $dur = if ($p.NaturalDuration.HasTimeSpan) { $p.NaturalDuration.TimeSpan.TotalSeconds } else { 6 }
    $p.Play()
    Start-Sleep -Milliseconds ([int](($dur + 0.4) * 1000))
    $p.Close()
}

# --- Fallback local: voz del sistema via WinRT ---
function Invoke-VozLocal($texto) {
    [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media, ContentType = WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
    Add-Type -AssemblyName System.Runtime.WindowsRuntime

    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

    function Await($op, $type) {
        $t = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
        $t.Wait(-1) | Out-Null
        $t.Result
    }

    $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
    $voces = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices
    $es = $voces | Where-Object { $_.Language -like 'es*' }
    $v = $es | Where-Object { $_.DisplayName -match 'Natural|Online' } | Select-Object -First 1
    if (-not $v) { $v = $es | Where-Object { $_.DisplayName -like '*Laura*' } | Select-Object -First 1 }
    if (-not $v) { $v = $es | Select-Object -First 1 }
    if ($v) { $synth.Voice = $v }

    $stream = Await $synth.SynthesizeTextToStreamAsync($texto) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
    $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
    Await $reader.LoadAsync($stream.Size) ([uint32]) | Out-Null
    $bytes = New-Object byte[] $stream.Size
    $reader.ReadBytes($bytes)
    $reader.Dispose()

    $wav = Join-Path $env:TEMP ("claude-voz-" + $PID + ".wav")
    [System.IO.File]::WriteAllBytes($wav, $bytes)
    try { (New-Object System.Media.SoundPlayer $wav).PlaySync() }
    finally { Remove-Item $wav -Force -ErrorAction SilentlyContinue }
}

try {
    if (-not (Test-Path $mp3)) { Invoke-EdgeTts $texto $voz $mp3 }
    Invoke-PlayMp3 $mp3
} catch {
    try { Invoke-VozLocal $texto } catch { }
}

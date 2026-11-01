# Sobe o ambiente local inteiro do GeoRural DataHub: Oracle, Airflow, back e front.
#
# Uso (na raiz do Atlaz4):   .\build
#   .\build -SemAirflow      sobe tudo menos o Airflow
#   Para desligar:           .\stop
#
# Por padrão usa os submódulos backend/ e frontend/ (git submodule update --init).
# Se você trabalha em outras pastas, ou precisa de outro comando para o back, copie
# build.local.example.ps1 para build.local.ps1 e ajuste (esse arquivo não vai para o git).

param([switch]$SemAirflow)

$PastaBack    = Join-Path $PSScriptRoot 'backend'
$PastaFront   = Join-Path $PSScriptRoot 'frontend'
$PastaAirflow = Join-Path $PSScriptRoot 'infra\airflow'
$ComandoBack  = '.\mvnw.cmd spring-boot:run'

$configLocal = Join-Path $PSScriptRoot 'build.local.ps1'
if (Test-Path $configLocal) { . $configLocal }

function Test-PortaOcupada($porta) {
    [bool](Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue)
}

# Abre uma janela nova do PowerShell rodando o comando, para cada parte ter os seus logs.
function Start-Janela($titulo, $pasta, $comando) {
    $script = "`$Host.UI.RawUI.WindowTitle = '$titulo'; $comando"
    Start-Process powershell -WorkingDirectory $pasta `
        -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $script
}

# --- Conferências antes de subir qualquer coisa
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error 'O Docker não está rodando. Abra o Docker Desktop e tente de novo.'
    exit 1
}
if (-not (Test-Path (Join-Path $PastaBack 'pom.xml'))) {
    Write-Error "Back-end não encontrado em $PastaBack. Rode 'git submodule update --init' ou ajuste o build.local.ps1."
    exit 1
}
if (-not (Test-Path (Join-Path $PastaFront 'package.json'))) {
    Write-Error "Front-end não encontrado em $PastaFront. Rode 'git submodule update --init' ou ajuste o build.local.ps1."
    exit 1
}
if (-not (Test-Path (Join-Path $PastaBack '.env'))) {
    Write-Warning "Falta o .env do back-end em $PastaBack (copie do .env.example)."
}

# --- 1. Oracle
Write-Host '1/4 Oracle' -ForegroundColor Cyan
docker compose -f (Join-Path $PastaBack 'docker-compose.yaml') up -d
if ($LASTEXITCODE -ne 0) { exit 1 }

# Na primeira subida o banco leva alguns minutos para ser criado; o back e o Airflow
# só conseguem conectar depois disso.
Write-Host '    Esperando o banco ficar pronto...' -NoNewline
$limite = (Get-Date).AddMinutes(10)
do {
    docker exec oracle healthcheck.sh *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Write-Host '.' -NoNewline
    Start-Sleep -Seconds 5
} while ((Get-Date) -lt $limite)
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Error "O Oracle não ficou pronto em 10 minutos. Veja o que houve com 'docker logs oracle'."
    exit 1
}
Write-Host ' pronto!' -ForegroundColor Green

# --- 2. Airflow
Write-Host '2/4 Airflow' -ForegroundColor Cyan
$envAirflow = Join-Path $PastaAirflow '.env.local'
if ($SemAirflow) {
    Write-Host '    Pulado (-SemAirflow).'
} elseif (-not (Test-Path $envAirflow)) {
    Write-Warning "Pulado: falta $envAirflow (copie do .env.local.example)."
} else {
    docker compose -f (Join-Path $PastaAirflow 'docker-compose.local.yaml') --env-file $envAirflow up -d --build
    Write-Host '    http://localhost:8088'
}

# --- 3. Back-end
Write-Host '3/4 Back-end' -ForegroundColor Cyan
if (Test-PortaOcupada 8080) {
    Write-Host '    Já tem algo rodando na porta 8080; não abri outro.'
} else {
    Start-Janela 'Atlaz4 - back' $PastaBack $ComandoBack
    Write-Host '    Abrindo em outra janela: http://localhost:8080/api/v1'
}

# --- 4. Front-end
Write-Host '4/4 Front-end' -ForegroundColor Cyan
if (Test-PortaOcupada 5173) {
    Write-Host '    Já tem algo rodando na porta 5173; não abri outro.'
} else {
    # Variável de ambiente ganha do .env.development: o front sempre aponta para o back real.
    $comandoFront = "`$env:VITE_API_URL = 'http://localhost:8080/api/v1'; "
    if (-not (Test-Path (Join-Path $PastaFront 'node_modules'))) { $comandoFront += 'npm install; ' }
    $comandoFront += 'npm run dev'
    Start-Janela 'Atlaz4 - front' $PastaFront $comandoFront
    Write-Host '    Abrindo em outra janela: http://localhost:5173'
}

Write-Host ''
Write-Host 'Tudo no ar. Para desligar: .\stop' -ForegroundColor Green

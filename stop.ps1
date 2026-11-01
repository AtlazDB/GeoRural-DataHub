# Desliga o que o .\build subiu: back, front, Airflow e Oracle.
# Os dados do banco ficam guardados no volume do Docker; nada é apagado.
#
# Uso (na raiz do Atlaz4):   .\stop

$PastaBack    = Join-Path $PSScriptRoot 'backend'
$PastaAirflow = Join-Path $PSScriptRoot 'infra\airflow'

$configLocal = Join-Path $PSScriptRoot 'build.local.ps1'
if (Test-Path $configLocal) { . $configLocal }

# Back e front: encerra só se quem ocupa a porta for mesmo o Java (back) ou o Node (front).
foreach ($parte in @(@{ Nome = 'Back-end'; Porta = 8080; Processo = 'java' },
                     @{ Nome = 'Front-end'; Porta = 5173; Processo = 'node' })) {
    $conexoes = Get-NetTCPConnection -LocalPort $parte.Porta -State Listen -ErrorAction SilentlyContinue
    $processos = $conexoes | Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue } |
        Where-Object { $_.ProcessName -eq $parte.Processo }
    if ($processos) {
        $processos | Stop-Process -Force
        Write-Host "$($parte.Nome) desligado." -ForegroundColor Cyan
    } else {
        Write-Host "$($parte.Nome) não estava rodando."
    }
}

$envAirflow = Join-Path $PastaAirflow '.env.local'
if (Test-Path $envAirflow) {
    Write-Host 'Airflow' -ForegroundColor Cyan
    docker compose -f (Join-Path $PastaAirflow 'docker-compose.local.yaml') --env-file $envAirflow down
}

Write-Host 'Oracle' -ForegroundColor Cyan
docker compose -f (Join-Path $PastaBack 'docker-compose.yaml') down

Write-Host 'Tudo desligado. As janelas do back e do front podem ser fechadas.' -ForegroundColor Green

# Teste rápido da sincronização
# Execute este script para debugar o problema

Write-Host "🔧 Testando sincronização com Mercado Livre..." -ForegroundColor Cyan

# Testa se o servidor está respondendo
try {
    Write-Host "`n1️⃣ Testando conexão com o servidor..." -ForegroundColor Yellow
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:5000/devolucoes" -Method GET -TimeoutSec 5
    Write-Host "✅ Servidor respondendo (Status: $($response.StatusCode))" -ForegroundColor Green
}
catch {
    Write-Host "❌ ERRO: Servidor não está respondendo!" -ForegroundColor Red
    Write-Host "   $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "`n   👉 Certifique-se que rodou: python app.py" -ForegroundColor Yellow
    exit 1
}

# Testa a API de sincronização
try {
    Write-Host "`n2️⃣ Testando API de sincronização..." -ForegroundColor Yellow

    $headers = @{
        "Content-Type" = "application/json"
    }

    $body = "{}"

    $response = Invoke-WebRequest `
        -Uri "http://127.0.0.1:5000/api/devolucoes/sincronizar-ml" `
        -Method POST `
        -Headers $headers `
        -Body $body `
        -TimeoutSec 30 `
        -ErrorAction Stop

    Write-Host "✅ API respondendo (Status: $($response.StatusCode))" -ForegroundColor Green

    # Mostra a resposta
    $data = $response.Content | ConvertFrom-Json

    Write-Host "`n📊 Resultado da sincronização:" -ForegroundColor Cyan
    Write-Host "   Mensagem: $($data.mensagem)"
    Write-Host "   Total pedidos: $($data.total)"
    Write-Host "   Criadas: $($data.criadas)"
    Write-Host "   Atualizadas: $($data.atualizadas)"

    if ($data.resumo) {
        Write-Host "`n📈 Resumo:" -ForegroundColor Cyan
        Write-Host "   Para revisão: $($data.resumo.para_revisao)"
        Write-Host "   Para retirar: $($data.resumo.para_retirar)"
        Write-Host "   Outros: $($data.resumo.outros_problemas)"
    }

    if ($data.erros -and $data.erros.Count -gt 0) {
        Write-Host "`n⚠️ Erros encontrados:" -ForegroundColor Yellow
        foreach ($erro in $data.erros) {
            Write-Host "   - $erro"
        }
    }
}
catch {
    Write-Host "❌ ERRO ao sincronizar!" -ForegroundColor Red
    Write-Host "   Status: $($_.Exception.Response.StatusCode)" -ForegroundColor Red
    Write-Host "   Mensagem: $($_.Exception.Message)" -ForegroundColor Red

    try {
        $errorResponse = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($errorResponse)
        $errorBody = $reader.ReadToEnd()
        Write-Host "`n   Resposta do servidor:" -ForegroundColor Yellow
        Write-Host "   $errorBody" -ForegroundColor Yellow
    }
    catch {
        # Ignora erro ao ler resposta
    }

    exit 1
}

Write-Host "`n✅ Teste concluído!" -ForegroundColor Green

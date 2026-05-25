# ml-devolucoes

Projeto Flask separado para acessar somente o modulo **Gerenciar Devolucoes**.

Este projeto agora roda tudo no proprio Flask: tela, API, banco SQLite, upload de evidencias e sincronizacao Mercado Livre. Nao depende de backend Express, frontend React, Vite ou Node.

## Estrutura

- `app.py`: login local, rotas Flask, API de devolucoes, OAuth Mercado Livre e SQLite.
- `data/devolucoes.sqlite`: banco local.
- `uploads/`: evidencias enviadas pela tela.
- `templates/` e `static/`: front proprio em HTML/CSS/JS.

## Variaveis obrigatorias

Para login local:

- `FLASK_SECRET_KEY`
- `PIN_MERCADO_LIVRE`

Para sincronizacao e acoes no Mercado Livre:

- `ML_CLIENT_ID`
- `ML_CLIENT_SECRET`
- `ML_USER_ID`
- `ML_ACCESS_TOKEN` ou `ML_REFRESH_TOKEN`
- `ML_REDIRECT_URI` quando usar callback publico ou porta/host diferente

O arquivo `.env` foi copiado do projeto original com os valores locais existentes. Nao publique esse arquivo.

## Instalar e rodar

Requisitos: Python 3.11+.

```powershell
cd "C:\Users\mansa\OneDrive\Área de Trabalho\ml-devolucoes"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Ao abrir `http://127.0.0.1:5000`, informe o PIN do Mercado Livre e acesse a tela de devolucoes.

Os pacotes Node ja foram mantidos na copia. Se precisar reinstalar:
Nao ha etapa de `npm install`.

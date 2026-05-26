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

macOS / Linux:

```bash
cd "/Users/julio/Documents/Antigra/warehouse-picker v2/Devoluçao"
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
APP_HOST=127.0.0.1 APP_PORT=5010 venv/bin/python app.py
```

Windows (PowerShell):

```powershell
cd "C:\caminho\para\Devolucao"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Na maquina NVS, use uma porta separada do Warehouse Picker, por exemplo:

```env
APP_HOST=127.0.0.1
APP_PORT=5010
```

Ao abrir `http://127.0.0.1:5010`, informe o PIN do Mercado Livre e acesse a tela de devolucoes.

Front e API rodam no mesmo processo Flask. Nao ha frontend Vite em `5173` nem backend Express em `3001` nesta versao.

## Rodar testes

```bash
venv/bin/python -m py_compile app.py
venv/bin/python -m unittest discover -s tests -v
```

## Endpoints relevantes

| Metodo | Rota | O que faz |
|---|---|---|
| `POST` | `/api/devolucoes/sincronizar-ml` | botao Atualizar ML (refresh do cache) |
| `GET` | `/api/devolucoes/filtros-ml` | resumo por bucket (lido do cache) |
| `GET` | `/api/devolucoes/cards?bucket=...` | lista de cards do bucket selecionado |
| `GET` | `/api/devolucoes/sync-diagnostico` | ultimas execucoes de sync e contagens |
| `GET` | `/api/devolucoes/sync-trace/ultimo` | trace detalhado do ultimo refresh |
| `POST` | `/api/pedidos/importar` | importa pedido manual por id/rastreio |
| `POST` | `/api/devolucoes/<id>/chegada` | confirma chegada (chama ML quando "esperado") |

Lista completa em `HANDOFF_CLAUDE.md`.

## Documentacao

| Doc | Para que serve |
|---|---|
| `HANDOFF_CLAUDE.md` | Passagem de bastao + estado atual + pendencias |
| `ENTENDER_DEVOLUCOES.md` | Arquitetura + fluxo de dados + decisoes |
| `TRABALHAR_DEVOLUCOES.md` | Guia de tarefas comuns + debug |
| `DEVOLUCOES_RESUMO_RAPIDO.md` | Resumo de 3 min |
| `DEVOLUCOES_DIAGRAMA.txt` | Diagrama ASCII completo |

Doc oficial ML (referencia): https://developers.mercadolivre.com.br/pt_br/gerenciar-devolucoes

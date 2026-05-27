# Passagem de Bastao - Projeto Devolucao ML

Atualizado: 2026-05-26
Projeto local: `/Users/julio/Documents/Antigra/warehouse-picker v2/Devoluçao`
URL local: `http://127.0.0.1:5010/devolucoes`

## Objetivo

Bater os numeros da aba **Devolucoes > Proximas a serem atendidas** do Mercado Livre dentro do painel local da NVS e, ao clicar em cada bucket, abrir os cards reais com produto, valor e motivo. Tudo via Flask mono, sem auto-refresh.

## Estado do Produto

Roda local em `http://127.0.0.1:5010`. Botao **Atualizar ML** dispara `POST /api/devolucoes/sincronizar-ml` que repopula o cache `ml_claim_classifications` e atualiza os cards.

```
Para sua revisao: 2
Para retirar no correio: 0
Outros problemas: 21
Total: 23
```

Bate com o painel ML.

## Arquitetura Atual

Flask monolitico em uma unica porta (default 5010). Front, API, banco SQLite, uploads e sincronizacao ML rodam no mesmo processo Python. Nao depende de Node, Express, React ou Vite.

- `app.py` rotas Flask, OAuth ML, classifier, cache, endpoints REST
- `templates/devolucoes.html` UI completa (HTML + JS inline)
- `static/styles.css` estilos
- `data/devolucoes.sqlite` banco local
- `uploads/` evidencias
- `tests/test_ml_contract.py` cobertura unitaria (9 testes)

## Regra de Classificacao (`actions-v3`) - CONGELADA NA BIBLIA

A chave eh `players[].available_actions[].action` no detalhe do claim. Em `app.py`:

```python
ML_CLASSIFIER_VERSION = "actions-v3"
ML_ENRICHMENT_VERSION = "enrich-v1"
```

Buckets via `classify_ml_live_queue_claim`:

| Bucket | Acao gatilho | Comentario |
|---|---|---|
| `para_revisao` | `return_review_unified_ok` ou `return_review_unified_fail` | Sufixo `_unified` valida com print real do ML. Doc publica menciona apenas `return_review_ok/fail` sem sufixo |
| `outros_problemas` | `send_message_to_mediator` | Mediacao em andamento |
| `para_retirar` | `return_status=label_generated` + `reason_id=PDD9967` | Retirada em agencia |
| `outros_problemas` (return) | `return_status in {label_generated, shipped, in_return, processing}` | Cobre devolucoes em transito sem mediator |
| `fora_da_fila` | resto | Nao conta no painel |

`apply_ml_queue_window` corta `outros_problemas` ao top N por `last_updated DESC` (default `ML_LIVE_QUEUE_OUTROS_LIMIT=21`).

**Importante:** a janela eh idempotente. Itens rebaixados em refresh anterior (com regra `:outside_recent_window`) sao restaurados ao bucket natural no inicio da proxima execucao. Sem isso, o cache contaminava as proximas sincronizacoes (caso classico: top 21 virava top 20).

## Fluxo "Atualizar ML"

1. JS chama `POST /api/devolucoes/sincronizar-ml` (timeout 90s)
2. Backend roda `refresh_ml_classification_cache(user_id)`:
   - `ml_live_claims_for_queue` busca 4 fontes (returns/mediations × opened/closed) com pre-filtro `claim_has_listed_seller_action`
   - `inspect_claim_for_queue` em paralelo (ThreadPoolExecutor 4 workers):
     - cache hit se `last_updated` + `classifier_version` + `enrichment_version` batem
     - cache miss: busca claim + return + order, enriquece com produto/valor/imagem/motivo/mandatory/due_date
   - `apply_ml_queue_window` aplica idempotente
   - `UPDATE ml_claim_classifications SET active=0` e `active=1` apenas nos atuais
3. Retorna resumo do cache para o JS
4. JS aplica resumo nos 3 cards (`paraRevisao`, `paraRetirar`, `outrosProblemas`) e total

## Fluxo "Clicar em bucket"

1. Click em `summary-item[data-bucket="para_revisao"]` chama `abrirPainelCardsBucket("para_revisao", "Para sua revisao")`
2. `GET /api/devolucoes/cards?bucket=para_revisao` retorna lista do cache ordenada por `due_date asc, last_updated desc`
3. JS renderiza grid de cards com produto, valor, motivo, imagem
4. Click em "Abrir fluxo" no card:
   - `POST /api/pedidos/importar` (importa o pedido para `devolucoes` local)
   - `abrirDetalhe(devolucao.id, "modal")` re-renderiza o modal flutuante com o detalhe completo (checklist, evidencias, contestacao, historico, botoes "Chegou esperado" / "Nao chegou")

## Tabelas SQLite

- `devolucoes` operacional pos-import (checklist, evidencias, contestacoes, historico vinculados aqui)
- `ml_claim_classifications` **fonte unica dos cards e do modal de bucket** (chave `claim_id`, colunas enriquecidas: `produto_nome`, `produto_imagem`, `valor_pago`, `taxa_venda`, `ml_tipo_logistica`, `motivo_label`, `pack_id`, `mandatory`, `due_date`, `date_created`)
- `ml_sync_runs` + `ml_raw_payloads` + `ml_reconciliation_diffs` + `ml_trace_events` trace e auditoria
- `historico_status`, `checklists`, `evidencias`, `contestacoes` fluxo operacional

`init_database` faz `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN` condicional para colunas extras (migracao incremental).

## Endpoints Principais

| Metodo | Rota | Descricao |
|---|---|---|
| `GET` | `/devolucoes` | tela principal |
| `POST` | `/api/devolucoes/sincronizar-ml` | refresh do cache (botao Atualizar ML) |
| `GET` | `/api/devolucoes/filtros-ml` | resumo dos buckets (lido do cache) |
| `GET` | `/api/resumo-ml` | mesmo resumo, formato alternativo |
| `GET` | `/api/devolucoes/cards?bucket=para_revisao\|para_retirar\|outros_problemas` | lista de cards do bucket (lida do cache) |
| `GET` | `/api/devolucoes/fila-ml-live` | versao "ao vivo" sem cache (mais lenta) |
| `GET` | `/api/devolucoes` | lista da tabela `devolucoes` local (fluxo operacional) |
| `POST` | `/api/pedidos/importar` | importa pedido por ID/rastreio e cria/atualiza row em `devolucoes` |
| `POST` | `/api/devolucoes/{id}/chegada` | confirma chegada (chama ML quando esperado) |
| `GET` | `/api/devolucoes/sync-trace/ultimo` | ultimo trace de sync |
| `GET` | `/api/devolucoes/sync-trace/{trace_id}` | trace especifico |
| `POST` | `/api/devolucoes/sincronizar-ml-completo` | fluxo legado pesado (nao usado pelo botao) |

## Variaveis de Configuracao

```env
FLASK_SECRET_KEY=...
PIN_MERCADO_LIVRE=1234
APP_HOST=127.0.0.1
APP_PORT=5010

ML_CLIENT_ID=...
ML_CLIENT_SECRET=...
ML_USER_ID=...
ML_ACCESS_TOKEN=...
ML_REFRESH_TOKEN=...
ML_REDIRECT_URI=...

ML_LIVE_QUEUE_MAX_PAGES=3
ML_LIVE_QUEUE_CLOSED_PAGES=1
ML_LIVE_QUEUE_OUTROS_LIMIT=21
ML_LIVE_QUEUE_WORKERS=4
```

`.env` tem tokens reais. **Nunca commitar.**

## Comandos Uteis

Rodar testes:

```bash
cd "/Users/julio/Documents/Antigra/warehouse-picker v2/Devoluçao"
venv/bin/python -m py_compile app.py
venv/bin/python -m unittest discover -s tests -v
```

Subir servidor:

```bash
APP_HOST=127.0.0.1 APP_PORT=5010 venv/bin/python app.py
```

Ver processo na porta:

```bash
lsof -nP -iTCP:5010 -sTCP:LISTEN
```

Consultar resumo via test_client:

```bash
venv/bin/python - <<'PY'
from app import app, PIN_MERCADO_LIVRE
with app.test_client() as c:
    c.post('/login', data={'pin': PIN_MERCADO_LIVRE})
    print(c.get('/api/devolucoes/filtros-ml').get_json())
PY
```

Forcar refresh do cache:

```bash
venv/bin/python - <<'PY'
from app import refresh_ml_classification_cache, current_env, init_database
init_database()
print(refresh_ml_classification_cache(current_env().get('ML_USER_ID')))
PY
```

Inspecionar distribuicao de buckets:

```bash
venv/bin/python - <<'PY'
from app import db
with db() as conn:
    for r in conn.execute("SELECT bucket, COUNT(*) c FROM ml_claim_classifications WHERE active=1 GROUP BY bucket"):
        print(r['bucket'], r['c'])
PY
```

## Riscos e Pendencias

1. **Regra `unified-only`**: doc publica menciona apenas `return_review_ok/fail`. Se ML emitir essa variante sem `_unified`, o sistema pode perder claim. Risco baixo enquanto ML mantiver o sufixo em producao, mas vale monitorar.

2. **`ML_LIVE_QUEUE_OUTROS_LIMIT=21` hardcoded**: a janela "21 mais recentes" bate com o print do ML mas regra oficial nao esta documentada. Pode mudar.

3. **`reason_id=PDD9967` (retirar correio) nao documentado** na doc publica. Caminho oficial seria resolver via `GET /post-purchase/v1/returns/reasons?flow=seller_return_failed&claim_id=...`.

4. **Endpoint legado `/api/devolucoes/sincronizar-ml-completo`** ainda existe e roda fluxo pesado. Nao eh chamado pelo botao mas continua disponivel.

5. **`mandatory` + `due_date`** persistidos no cache mas nao sao usados para priorizacao alem da ordenacao do endpoint `/cards`. Pode evoluir para ordenacao na UI.

6. **Cache miss inicial**: ao mudar `ML_CLASSIFIER_VERSION` ou `ML_ENRICHMENT_VERSION`, todo o cache eh invalidado. Proximo refresh leva ~20-30s para repopular (23 claims × 3 chamadas ML em 4 workers).

## Historico de Iteracoes

### Etapa 1: paridade de numeros
- classifier `actions-v3` baseado em `available_actions`
- cache `ml_claim_classifications` com idempotencia de `active=0/1`
- botao "Atualizar ML" passou a chamar apenas `refresh_ml_classification_cache` (fluxo legado pesado removido do botao)
- batia 23/2/21 com ML

### Etapa 2: cards corretos no modal
- problema: contador correto, mas modal mostrava 7 itens irrelevantes
- causa: contador lia cache, modal filtrava tabela `devolucoes` local com regra legada
- fix: schema do cache ganhou colunas enriquecidas, novo endpoint `/api/devolucoes/cards`, JS refatorado para usar a mesma fonte do contador

### Etapa 3: modal de detalhe
- problema: "Abrir fluxo" no card aparentava voltar a tela inicial
- causa: `abrirDetalhe` renderiza no `#detalhe` (aside) mas CSS tinha `display:none` no aside
- fix: `abrirDetalhe(id, target)` aceita `target="modal"` e renderiza dentro do `modalPainelFlutuante`

### Etapa 4: F5 mostrava numeros distorcidos
- problema: F5 mostrava 9/2/41 (contador local), Atualizar ML mostrava 2/0/21
- causa: `carregarResumoML()` definida mas nunca chamada no boot
- fix: `carregarTudo()` chama `carregarResumoML()` em paralelo com `/api/devolucoes`

### Etapa 5: janela contaminava cache
- problema: ML mostrava 21 em outros_problemas, sistema mostrava 20
- causa: `apply_ml_queue_window` rebaixava itens a `fora_da_fila` no cache; refresh seguinte ja recebia cache contaminado e operava sobre subconjunto menor
- fix: window restaura bucket natural e tira sufixo `:outside_recent_window` antes de aplicar janela (idempotencia)

## Proximos Passos Recomendados

1. Monitorar se `return_review_ok/fail` sem `_unified` ja aparece em algum claim real e adaptar regra.
2. Considerar usar `mandatory=1` + `due_date` proximo de hoje para sinalizar urgencia visual nos cards.
3. Avaliar se vale exibir um banner quando `ML_LIVE_QUEUE_OUTROS_LIMIT` ficar saturado (sinal que o limite mudou no ML).
4. Limpar docs legadas remanescentes (RESUMO_RAPIDO, ENTENDER, TRABALHAR, DIAGRAMA) jah feita junto desta atualizacao.

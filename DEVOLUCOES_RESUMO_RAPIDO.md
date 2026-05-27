# Resumo Rapido - Modulo de Devolucoes

Leitura: 3 min.

## O que e?

Painel interno NVS para operar devolucoes do **Mercado Livre**, replicando a aba "Proximas a serem atendidas" do painel pos-venda. Decisao por claim, registro de checklist/evidencias e geracao de mensagem de mediacao.

## Arquitetura em 30 segundos

```
┌─ Flask mono (porta 5010) ────────────────────────────────┐
│  app.py                                                   │
│    └─ rotas, classifier, cache, OAuth ML, uploads        │
│  templates/devolucoes.html (UI inline + JS)              │
│  static/styles.css                                        │
│  data/devolucoes.sqlite                                   │
│  uploads/                                                 │
└───────────────────────────────────────────────────────────┘
                       ↓ HTTP (OAuth Bearer)
              api.mercadolibre.com (post-purchase)
```

Nao tem Node, Express, React, Vite ou processo paralelo. Tudo num unico processo Python.

## Estrutura

| O que | Onde |
|---|---|
| Backend + UI + classifier | `app.py` |
| Template + JS | `templates/devolucoes.html` |
| CSS | `static/styles.css` |
| Banco | `data/devolucoes.sqlite` |
| Testes | `tests/test_ml_contract.py` |

## Como rodar

```bash
cd "/Users/julio/Documents/Antigra/warehouse-picker v2/Devoluçao"
venv/bin/python app.py
# abrir http://127.0.0.1:5010
# PIN = $PIN_MERCADO_LIVRE no .env (default 1234)
```

## O que voce ve em `/devolucoes`

```
╔════════════════════════════════════════════════════╗
║ ENTRADA DE DEVOLUCAO              [Atualizar ML]   ║
║ [Pedido, pacote ou rastreio]      [Buscar venda]   ║
╠════════════════════════════════════════════════════╣
║ Proximas a serem atendidas                 23      ║
║   Para sua revisao                          2      ║
║   Para retirar no correio                   0      ║
║   Outros problemas                         21      ║
╠════════════════════════════════════════════════════╣
║ PENDENCIAS                                         ║
║ Em andamento (checklists iniciados) [Visualizar]   ║
╚════════════════════════════════════════════════════╝
```

Click em um bucket abre modal flutuante com os cards (produto, valor, motivo, imagem). Click em **Abrir fluxo** num card abre o detalhe completo (chegada, checklist, evidencias, contestacao, historico) no mesmo modal.

## Fluxo de uma devolucao

```
1. Cliente solicita devolucao no ML
   ↓
2. ML coloca claim em Proximas a serem atendidas
   ↓
3. Usuario clica Atualizar ML no painel local
   ↓ POST /api/devolucoes/sincronizar-ml
4. Backend repopula cache ml_claim_classifications
   ↓
5. Cards exibem numeros que batem com ML
   ↓
6. Usuario clica no bucket "Para sua revisao"
   ↓ GET /api/devolucoes/cards?bucket=para_revisao
7. Modal mostra cards do bucket
   ↓
8. Usuario clica Abrir fluxo num card
   ↓ POST /api/pedidos/importar
9. Modal troca para tela de detalhe (chegada, checklist, etc)
   ↓ POST /api/devolucoes/{id}/chegada
10. Decisao registrada local + ML (quando "esperado")
```

## Classificacao (`actions-v3`)

A regra olha `players[].available_actions[].action` no detalhe do claim:

| Bucket | Acao gatilho |
|---|---|
| `para_revisao` | `return_review_unified_ok` / `return_review_unified_fail` |
| `outros_problemas` | `send_message_to_mediator` |
| `para_retirar` | `return_status=label_generated` + `reason_id=PDD9967` |

## Comandos rapidos

```bash
# rodar
APP_HOST=127.0.0.1 APP_PORT=5010 venv/bin/python app.py

# testar
venv/bin/python -m unittest discover -s tests -v

# inspecionar buckets atuais
venv/bin/python -c "from app import db
with db() as c:
    for r in c.execute('SELECT bucket, COUNT(*) FROM ml_claim_classifications WHERE active=1 GROUP BY bucket'):
        print(r[0], r[1])"

# forcar refresh do cache (equivale ao botao)
venv/bin/python -c "from app import refresh_ml_classification_cache, current_env, init_database
init_database()
print(refresh_ml_classification_cache(current_env().get('ML_USER_ID')))"
```

## Problemas comuns

**Card mostra numero diferente do ML**
→ click em "Atualizar ML". Se persistir, ver `app.py` (`apply_ml_queue_window` ou regra do classifier).

**F5 mostra numeros distorcidos**
→ ja corrigido. `carregarTudo` chama `carregarResumoML` no boot. Se voltar, conferir `templates/devolucoes.html` linha `carregarTudo`.

**Click em "Abrir fluxo" parece voltar pra tela inicial**
→ ja corrigido. `abrirDetalhe(id, "modal")` renderiza no modal. Se voltar, conferir CSS `.center-workspace .meli-detail { display: none; }`.

**Erro 500 ao clicar bucket**
→ provavel schema desatualizado. Reiniciar Flask roda `init_database` que faz `ALTER TABLE` condicional.

## Referencias internas

| Doc | Para que serve |
|---|---|
| `HANDOFF_CLAUDE.md` | Estado completo + endpoints + pendencias |
| `ENTENDER_DEVOLUCOES.md` | Arquitetura detalhada + fluxo de dados |
| `TRABALHAR_DEVOLUCOES.md` | Guia de tarefas comuns |
| `DEVOLUCOES_DIAGRAMA.txt` | Diagrama ASCII completo |

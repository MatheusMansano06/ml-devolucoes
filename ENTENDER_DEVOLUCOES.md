# Entendendo o Modulo de Devolucoes

Leitura: 20 min. Foco em arquitetura + fluxo de dados + decisoes de design.

## Visao Geral

Painel local Flask que replica a aba **Devolucoes > Proximas a serem atendidas** do Mercado Livre, com decisao por claim (chegada esperada / divergente), checklist de revisao, evidencias e mensagem para mediacao.

A versao atual eh **mono Flask** em uma unica porta. Versoes antigas tinham Node Express + React/Vite separados, mas foram extintas.

## Arquitetura

```
┌──────────────────────────────────────────────────────────────┐
│                      Flask App (porta 5010)                   │
│                                                                │
│  Rotas Flask              Funcoes Internas                    │
│  ──────────────          ──────────────────                  │
│  /devolucoes          →  render_template                      │
│  /api/devolucoes/...  →  classifier + cache + ML API          │
│  /api/pedidos/...     →  build_devolucao_from_identifier      │
│  /uploads/<file>      →  send_from_directory                  │
│  /login, /logout      →  session local com PIN                │
│                                                                │
│  Persistencia                                                  │
│  ─────────────                                                 │
│  data/devolucoes.sqlite                                        │
│   ├─ devolucoes (operacional pos-import)                      │
│   ├─ ml_claim_classifications (cache da fila ML)              │
│   ├─ checklists, evidencias, contestacoes                     │
│   ├─ historico_status                                          │
│   └─ ml_sync_runs, ml_raw_payloads, ml_trace_events           │
│                                                                │
│  Integracao externa                                            │
│  ──────────────────                                            │
│  api.mercadolibre.com (OAuth Bearer)                          │
│   ├─ /post-purchase/v1/claims/search                           │
│   ├─ /post-purchase/v1/claims/{id}                             │
│   ├─ /post-purchase/v2/claims/{id}/returns                     │
│   ├─ /post-purchase/v1/returns/{id}/reviews                    │
│   ├─ /post-purchase/v1/returns/{id}/return-review (POST)       │
│   ├─ /orders/{id}                                              │
│   └─ /items/{id}                                               │
└──────────────────────────────────────────────────────────────┘
```

## Arquivos e Responsabilidades

### `app.py`

Tudo em um arquivo. Secoes logicas:

- **bootstrap** (`init_database`, `db()`, `current_env`)
- **OAuth ML** (`ml_access_token`, `mercadolivre_auth_*`)
- **ML helpers** (`ml_get`, `ml_request`, `claim_available_actions`, `action_names`)
- **classifier** (`classify_ml_live_queue_claim`, `apply_ml_queue_window`)
- **cache** (`inspect_claim_for_queue`, `cached_claim_classification`, `save_claim_classification`, `refresh_ml_classification_cache`)
- **build de itens** (`build_ml_devolucao`, `build_devolucao_from_identifier`, `order_visuals`, `order_financials`)
- **operacional pos-import** (`upsert_ml_devolucao`, `existing_ml_devolucao`)
- **rotas REST** (`api_*`)

### `templates/devolucoes.html`

Single-page com JS inline. Componentes principais:

- entrada de pedido (input + botoes "Buscar venda" e "Atualizar ML")
- card resumo "Proximas a serem atendidas" com 3 itens clicaveis (revisao, retirar, outros)
- pendencias (checklists iniciados)
- modais: `modalPainelFlutuante` (cards do bucket + detalhe), `modalNova`, `modalPedidoConfirmacao`, `modalChegada`, `modalChecklistEtapas`

JS principal:

- `carregarTudo()` boot: chama `/api/devolucoes` + `carregarResumoML()` em paralelo
- `sincronizarMercadoLivre()` botao Atualizar ML
- `abrirPainelCardsBucket(bucket, titulo)` modal com cards do bucket
- `abrirDetalhe(id, target)` painel ou modal de detalhe
- `bindDetalhe()` event listeners do detalhe (chegada, checklist, evidencias, contestacao)

### `static/styles.css`

Layout responsivo. **Atencao:** `.center-workspace .meli-detail { display: none; }` esconde o aside lateral de detalhe; por isso o detalhe foi migrado para o modal flutuante.

### `data/devolucoes.sqlite`

Banco unico. Schema gerenciado pelo proprio `init_database` com `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN` condicional (migracao incremental).

## Fluxo de Dados Detalhado

### Atualizar ML (botao)

```
JS click em #sincronizarMl
  │
  ├─→ POST /api/devolucoes/sincronizar-ml
  │
  ├─→ api_sincronizar_ml()
  │   ├─ start_ml_sync_run("classification_cache")
  │   ├─ refresh_ml_classification_cache(user_id, sync_run_id, trace_id)
  │   │   │
  │   │   ├─ ml_live_claims_for_queue(user_id, max_pages=3)
  │   │   │   ├─ ml_claims_search returns/opened (3 pgs)
  │   │   │   ├─ ml_claims_search returns/closed (1 pg)
  │   │   │   ├─ ml_claims_search mediations/opened (3 pgs)
  │   │   │   ├─ ml_claims_search mediations/closed (1 pg)
  │   │   │   └─ filtra com claim_has_listed_seller_action
  │   │   │
  │   │   ├─ ThreadPool(4) inspect_claim_for_queue para cada claim
  │   │   │   ├─ cached_claim_classification? hit -> retorna payload
  │   │   │   ├─ miss -> ml_get /post-purchase/v1/claims/{id}
  │   │   │   │        + claim_return_info (returns endpoint)
  │   │   │   │        + fetch_order_for_claim (/orders/{id})
  │   │   │   │        + order_visuals (item, valor, taxa, full)
  │   │   │   │        + bucket_action_meta (mandatory, due_date)
  │   │   │   ├─ classify_ml_live_queue_claim -> bucket + regra
  │   │   │   └─ save_claim_classification(item)
  │   │   │
  │   │   ├─ apply_ml_queue_window(rows)
  │   │   │   ├─ restaura bucket natural (remove :outside_recent_window)
  │   │   │   ├─ ordena outros_problemas por last_updated DESC
  │   │   │   ├─ marca top 21 como ativo
  │   │   │   └─ rebaixa resto para fora_da_fila + sufixo na regra
  │   │   │
  │   │   ├─ UPDATE ml_claim_classifications SET active=0
  │   │   ├─ UPDATE ... SET active=1 WHERE claim_id IN (...)
  │   │   └─ retorna resumo via resumo_from_classification_cache()
  │   │
  │   ├─ finish_ml_sync_run(success/partial)
  │   └─ return jsonify({resumo, trace_id, ...})
  │
  └─→ JS recebe resumo
      ├─ aplicarResumoML(resumo) atualiza cards
      └─ renderUrgencias() recalcula totais locais
```

### Click em bucket (ex: Para sua revisao)

```
JS click em .summary-item[data-bucket="para_revisao"]
  │
  ├─→ abrirPainelCardsBucket("para_revisao", "Para sua revisao")
  │
  ├─→ GET /api/devolucoes/cards?bucket=para_revisao
  │
  ├─→ api_cards_por_bucket()
  │   └─ SELECT * FROM ml_claim_classifications
  │      WHERE active=1 AND bucket='para_revisao'
  │      ORDER BY due_date ASC, last_updated DESC
  │
  └─→ JS renderiza grid com bucketCardHTML(card)
      └─ click em "Abrir fluxo"
         │
         ├─→ POST /api/pedidos/importar { pedido_id }
         │   └─ build_devolucao_from_identifier + upsert_ml_devolucao
         │
         └─→ abrirDetalhe(devolucao.id, "modal")
             ├─ GET /api/devolucoes/{id}
             ├─ GET /api/devolucoes/{id}/historico
             ├─ GET /api/devolucoes/{id}/checklist
             ├─ GET /api/devolucoes/{id}/evidencias
             ├─ GET /api/devolucoes/{id}/contestacoes
             ├─ renderiza HTML dentro de #floatingPanelContent
             └─ bindDetalhe() ativa botoes chegouEsperado / naoChegouEsperado
```

### Confirmar "Chegou como esperado"

```
JS click em #chegouEsperado
  │
  ├─→ POST /api/devolucoes/{id}/chegada { resultado: "esperado" }
  │
  ├─→ api_chegada()
  │   ├─ ml_confirm_return_review_ok(claim_id)
  │   │   ├─ ml_get /post-purchase/v1/claims/{id}
  │   │   ├─ ml_get /post-purchase/v2/claims/{id}/returns
  │   │   ├─ POST /post-purchase/v1/returns/{return_id}/return-review (body {})
  │   │   └─ fallback: POST /post-purchase/v1/claims/{id}/actions/return-review-ok
  │   ├─ UPDATE devolucoes SET status='sem_divergencia', ml_ativo=0, requer_acao=0
  │   └─ INSERT historico_status
  │
  └─→ JS recarrega tudo
```

## Tabelas Principais

### `devolucoes`

Tabela operacional. Cada linha corresponde a uma devolucao **importada** (manual ou auto via `build_ml_devolucao`). Contem dados do produto, valor, status local, checklist atrelado etc.

Status validos em `STATUS_PERMITIDOS`: `aguardando_produto`, `em_transito`, `nao_recebido`, `produto_recebido`, `em_analise`, `divergencia_encontrada`, `sem_divergencia`, `contestacao_aberta`, `aguardando_plataforma`, `aprovado`, `parcial`, `reprovado`, `encerrado`.

`ml_ativo=1` indica que a devolucao ainda esta na fila do ML. Quando o usuario marca "Chegou esperado" e o ML aceita, vira `ml_ativo=0`.

### `ml_claim_classifications`

**Fonte de verdade dos cards e do modal de bucket.** Chave `claim_id`. Inclui:

- classificacao: `bucket`, `regra`, `last_updated`
- estado do claim: `status`, `stage`, `claim_type`, `reason_id`, `seller_actions`
- estado do return: `return_id`, `return_status`, `shipment_status`, `shipment_destination`
- enriquecimento visual: `produto_nome`, `produto_imagem`, `valor_pago`, `taxa_venda`, `ml_tipo_logistica`, `motivo_label`, `pack_id`
- urgencia: `mandatory`, `due_date`, `date_created`
- snapshot completo em `payload` (JSON)
- gating: `active` (1 = na fila atual, 0 = saiu)

Versionamento: `classifier_version` e `enrichment_version` no payload. Mudar essas constantes invalida o cache e forca repopulacao.

### Trace e auditoria

- `ml_sync_runs`: cada execucao do refresh (tipo, status, totais, detalhes JSON)
- `ml_raw_payloads`: snapshots brutos por `resource_type` + `resource_id`
- `ml_trace_events`: passos cronologicos por `trace_id`
- `ml_reconciliation_diffs`: divergencias detectadas

Util para diagnosticar discrepancias contra o painel ML.

## Decisoes de Design

### Por que cache em vez de chamar ML em todo render?

Painel ML retorna ~22600 claims totais (entre returns abertos/fechados e mediations). Buscar e classificar tudo a cada page load seria inviavel. O cache mantem apenas os ~45 claims com acao pendente listada, suficiente para o painel.

### Por que `enrichment_version` separado de `classifier_version`?

Permite evoluir as colunas visuais sem reclassificar tudo. Quando so visual muda (nova coluna), bumpa enrichment. Quando regra de bucket muda, bumpa classifier.

### Por que `apply_ml_queue_window` precisa ser idempotente?

Cache persiste o **resultado final** (pos-window) por claim. Se window so cortasse sem restaurar, o claim rebaixado em refresh anterior nao competiria pelo top no refresh seguinte. Resultado: top 21 virava top 20 (problema real observado).

Fix: window restaura bucket natural removendo o sufixo `:outside_recent_window` no inicio. Isso garante que cada refresh re-avalia do zero.

### Por que detalhe abre no modal e nao no aside?

CSS atual esconde `.center-workspace .meli-detail`. Em vez de mudar CSS (que afetaria layouts), `abrirDetalhe(id, target)` aceita `target="modal"` e renderiza dentro do `modalPainelFlutuante`. Outros call sites continuam usando `target="painel"` por default (compatibilidade).

### Por que `unified` actions em vez de `return_review_ok` puro?

Doc publica menciona apenas `return_review_ok` / `return_review_fail`. Mas o painel ML batia exato apenas quando filtramos por `return_review_unified_ok` / `return_review_unified_fail` (variantes que aparecem em producao). Risco conhecido: se ML emitir `_ok/_fail` sem sufixo em algum caso, perderemos. Solucao futura: aceitar ambas variantes.

## Diferencas vs Doc Oficial ML

| Aspecto | Doc oficial | Nosso codigo |
|---|---|---|
| Action de revisao | `return_review_ok` / `return_review_fail` | `return_review_unified_ok/fail` (validado em prod) |
| Reason `PDD9967` | Nao documentado | Usado como gatilho de `para_retirar` |
| Aba "Proximas a serem atendidas" | Nao tem API oficial | Engenharia reversa via `available_actions` |
| Janela "21 itens" | Nao documentado | `ML_LIVE_QUEUE_OUTROS_LIMIT=21` |

Doc base: https://developers.mercadolivre.com.br/pt_br/gerenciar-devolucoes

## Como Diagnosticar Problemas

1. **Numero nao bate com ML**: rodar `Atualizar ML`, depois verificar distribuicao por bucket no cache (`SELECT bucket, COUNT(*) WHERE active=1 GROUP BY bucket`)
2. **Claim sumiu**: ver `ml_trace_events` do ultimo `trace_id`; conferir `regra` em `ml_claim_classifications`
3. **Cards do bucket vazios mas contador > 0**: confirmar `active=1` no DB e que enrichment_version bate (campo `produto_nome` vazio = cache miss nao foi enriquecido)
4. **Token ML expirou**: clicar Autorizar ML novamente (rota `/mercadolivre/auth/start`)
5. **Sync demora muito**: aumentar `ML_LIVE_QUEUE_WORKERS`; reduzir `ML_LIVE_QUEUE_MAX_PAGES` se ainda assim demorar

Veja `TRABALHAR_DEVOLUCOES.md` para tarefas concretas comuns.

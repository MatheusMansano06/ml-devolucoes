# Trabalhar no Modulo de Devolucoes

Regra principal: seguir `BIBLIA_POS_VENDA_ML.md` como fonte de verdade para qualquer ajuste de pos-venda/devolucoes.

Leitura: 30 min. Foco em tarefas concretas: rodar, debugar, alterar codigo.

## Setup

```bash
cd "/Users/julio/Documents/Antigra/warehouse-picker v2/Devoluçao"
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env  # editar tokens ML
```

`.env` precisa ter `ML_CLIENT_ID`, `ML_CLIENT_SECRET`, `ML_USER_ID`, `ML_ACCESS_TOKEN` ou `ML_REFRESH_TOKEN`. Sem isso, sync ML falha.

## Rodar

```bash
APP_HOST=127.0.0.1 APP_PORT=5010 venv/bin/python app.py
# acessar http://127.0.0.1:5010
# PIN do .env (default 1234)
```

Em outro terminal, monitorar logs:

```bash
tail -f /tmp/devolucao_flask.log   # se rodou em background
```

## Testar

```bash
venv/bin/python -m py_compile app.py
venv/bin/python -m unittest discover -s tests -v
```

Tudo deve passar antes de qualquer commit.

## Cenarios Comuns

### 1. Mudar regra do classifier

Arquivo: `app.py`
Funcao: `classify_ml_live_queue_claim(claim, return_info)`

```python
def classify_ml_live_queue_claim(claim: dict, return_info: dict) -> tuple[str, str]:
    actions = set(action_names(claim))
    review_actions = {"return_review_unified_ok", "return_review_unified_fail"}
    if actions.intersection(review_actions):
        return "para_revisao", "seller_available_action:return_review"
    ...
```

**Apos qualquer mudanca:**

1. Bumpa `ML_CLASSIFIER_VERSION` (ex: `"actions-v3"` -> `"actions-v4"`)
2. Roda testes: `venv/bin/python -m unittest discover -s tests -v`
3. Reinicia Flask
4. Clica Atualizar ML (cache sera invalidado e repopulado)

### 2. Adicionar nova coluna ao cache

Arquivo: `app.py`
Funcao: `init_database()` + `save_claim_classification()` + `inspect_claim_for_queue()`

Passos:

1. Em `init_database`, adicionar ao dict `classification_extra_columns`:

```python
classification_extra_columns = {
    ...
    "nova_coluna": "TEXT DEFAULT ''",
}
```

2. Em `save_claim_classification`, adicionar ao INSERT + ON CONFLICT UPDATE + lista de parametros.

3. Em `inspect_claim_for_queue`, popular `item["nova_coluna"]`.

4. Bumpa `ML_ENRICHMENT_VERSION` (ex: `"enrich-v1"` -> `"enrich-v2"`).

5. Restart + Atualizar ML.

### 3. Adicionar novo bucket

Se quiser separar um novo grupo (ex: "aguardando_envio"):

1. Em `classify_ml_live_queue_claim`, adicionar branch que retorna `("aguardando_envio", "regra...")`.
2. Em `api_cards_por_bucket`, adicionar ao set `allowed_buckets`.
3. Em `templates/devolucoes.html`, adicionar `<button class="summary-item" data-bucket="aguardando_envio">`.
4. Em `resumo_from_classification_cache`, expor a contagem.
5. Em `apply_ml_queue_window`, decidir se o novo bucket tambem tem janela.
6. Bumpa `ML_CLASSIFIER_VERSION`.
7. Restart + Atualizar ML.

### 4. Debugar discrepancia com painel ML

Passo a passo:

```bash
# 1. ver contagem por bucket
venv/bin/python -c "from app import db
with db() as c:
    for r in c.execute('SELECT bucket, COUNT(*) FROM ml_claim_classifications WHERE active=1 GROUP BY bucket'):
        print(r[0], r[1])"

# 2. ver claims classificados como fora_da_fila por regra
venv/bin/python -c "from app import db
with db() as c:
    for r in c.execute(\"SELECT regra, COUNT(*) FROM ml_claim_classifications WHERE active=1 AND bucket='fora_da_fila' GROUP BY regra\"):
        print(r[0], r[1])"

# 3. ver claims com mediator que ficaram fora (suspeitos)
venv/bin/python -c "from app import db
with db() as c:
    for r in c.execute(\"SELECT claim_id, status, stage, regra FROM ml_claim_classifications WHERE active=1 AND seller_actions LIKE '%mediator%' AND bucket='fora_da_fila'\"):
        print(dict(r))"

# 4. inspecionar trace do ultimo refresh
venv/bin/python -c "from app import app, PIN_MERCADO_LIVRE
import json
with app.test_client() as c:
    c.post('/login', data={'pin': PIN_MERCADO_LIVRE})
    data = c.get('/api/devolucoes/sync-trace/ultimo').get_json()
    print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])"
```

### 5. Forcar refresh do cache sem usar a UI

```bash
venv/bin/python - <<'PY'
from app import refresh_ml_classification_cache, current_env, init_database
init_database()
result = refresh_ml_classification_cache(current_env().get("ML_USER_ID"))
print("declarados:", result["declarados"])
print("inspecionados:", result["inspecionados"])
print("cache_hits:", result["cache_hits"], "misses:", result["cache_misses"])
print("resumo:", result["resumo"])
print("erros:", result["erros"])
PY
```

### 6. Limpar cache (forcar repopulacao integral)

```bash
venv/bin/python -c "from app import db
with db() as c:
    c.execute('DELETE FROM ml_claim_classifications')
    print('cache limpo')"
```

Proximo Atualizar ML demora ~20-30s para repopular.

### 7. Re-autorizar ML (token expirou)

Abrir no navegador: `http://127.0.0.1:5010/mercadolivre/auth/start`. Aceitar permissoes. Sera redirecionado de volta com `ML_ACCESS_TOKEN` e `ML_REFRESH_TOKEN` atualizados no `.env`.

Se preferir CLI:

```bash
# o codigo refresh_token roda automaticamente quando ml_request recebe 401
# manualmente:
venv/bin/python -c "from app import ml_access_token; print(ml_access_token(force_refresh=True))"
```

### 8. Importar pedido manualmente (sem usar a UI)

```bash
venv/bin/python -c "from app import build_devolucao_from_identifier, upsert_ml_devolucao
item = build_devolucao_from_identifier('2000016385699074')
action = upsert_ml_devolucao(item)
print(action, item['ml_claim_id'])"
```

### 9. Inspecionar uma classificacao especifica

```bash
venv/bin/python -c "from app import db
import json
with db() as c:
    row = c.execute('SELECT * FROM ml_claim_classifications WHERE claim_id=?', ['5515780140']).fetchone()
    if row:
        d = dict(row)
        d['payload'] = json.loads(d['payload'])
        d['seller_actions'] = json.loads(d['seller_actions'])
        d['order_ids'] = json.loads(d['order_ids'])
        print(json.dumps(d, indent=2, ensure_ascii=False, default=str)[:4000])"
```

### 10. Alterar UI (template + JS inline)

Arquivo: `templates/devolucoes.html`. JS inline no fim do `<body>`.

Flask renderiza com `render_template`. Em modo nao-debug pode cachear template em memoria. **Reiniciar Flask** apos editar o template.

Estilos em `static/styles.css`. Hot reload nao tem; recarrega no navegador apos save.

### 11. Adicionar teste

Arquivo: `tests/test_ml_contract.py`. Classe: `MercadoLivreContractTests`.

Padrao para isolar DB:

```python
def test_minha_coisa(self):
    with tempfile.TemporaryDirectory() as tmpdir, patch.object(app, "DB_PATH", Path(tmpdir) / "test.sqlite"):
        app.init_database()
        # ...
```

Para testar endpoints:

```python
with app.app.test_client() as client:
    with client.session_transaction() as session:
        session["logged_in"] = True
    response = client.get("/api/devolucoes/cards?bucket=para_revisao")
```

### 12. Deploy / producao

Este projeto **nao tem deploy automatizado**. Roda local na maquina NVS. Se algum dia for para producao, considerar:

- WSGI server (gunicorn/uwsgi) em vez de `app.run`
- HTTPS via proxy reverso
- `ML_ACCESS_TOKEN` rotacionado
- `data/devolucoes.sqlite` em volume persistente

## Problemas Conhecidos

### Erro 500 ao clicar bucket

Schema desatualizado (ALTER nao rodou ainda). Reiniciar Flask roda `init_database` que aplica `ALTER TABLE ADD COLUMN` condicional.

### Cards vazios apos bumpar `ML_CLASSIFIER_VERSION`

Cache invalidado. Clicar Atualizar ML repopula (~20-30s).

### "Para sua revisao" diverge do ML por 1 ou 2

Verificar regra `unified` ainda eh suficiente. Pode ser que algum claim novo use `return_review_ok` sem sufixo. Adicionar essas variantes ao set `review_actions` em `classify_ml_live_queue_claim`.

### "Outros problemas" diverge do ML

Verificar `ML_LIVE_QUEUE_OUTROS_LIMIT`. Se ML mostrar 22 e local mostrar 21, ajustar para 22.

### F5 mostra numero estranho

Conferir que `carregarTudo()` chama `carregarResumoML()` no boot (parallel `Promise.all`). Bug historico ja corrigido mas voltar conferir.

### Click em "Abrir fluxo" parece nao fazer nada

CSS pode ter `display:none` no `#detalhe`. Solucao atual eh `abrirDetalhe(id, "modal")` que ignora o `#detalhe`. Conferir `templates/devolucoes.html` busca por `target = "painel"`.

## Convencoes

- Flask roda na 5010 (nao colidir com warehouse-picker em outra porta)
- Strings de cliente em portugues sem acentos no codigo (compatibilidade legacy)
- `motivo_label` mapeia PDD codes (ex: `PDD9939` -> "O comprador se arrependeu")
- Buckets: `para_revisao`, `para_retirar`, `outros_problemas`, `fora_da_fila`, `erro`
- Numeros do painel ML devem bater **exatos**. Se divergir, eh bug.

## Boa Pratica de Codigo

- **classifier eh sagrado**: nao tocar sem motivo real + testes. Buggy aqui = numeros errados na UI = perda de confianca.
- **`apply_ml_queue_window` deve ser idempotente**: nao introduzir estado que dependa de "primeira execucao".
- **Adicionar campo ao cache?** Bumpa enrichment_version. Sem isso, cache antigo retorna sem o campo.
- **Adicionar bucket?** Bumpa classifier_version. Sem isso, cache antigo retorna bucket errado.
- **Nao explodir `.env`** em logs ou respostas.
- **Trace tudo**: usar `add_ml_trace_event` para passos novos no refresh; facilita debug.

## Referencias

- doc oficial ML: https://developers.mercadolivre.com.br/pt_br/gerenciar-devolucoes
- `HANDOFF_CLAUDE.md`: estado completo + endpoints
- `ENTENDER_DEVOLUCOES.md`: arquitetura + decisoes de design
- `DEVOLUCOES_DIAGRAMA.txt`: diagrama ASCII

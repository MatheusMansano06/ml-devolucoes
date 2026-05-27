# Plano de Implementação - Conformidade com BIBLIA_POS_VENDA_ML.md

## Objetivo

Restaurar conformidade total com a BIBLIA_POS_VENDA_ML.md e criar mecanismo imutável para evitar futuras violações.

## O que será feito

### FASE 1: Reverter mudanças quebradas (URGENTE)

#### 1.1 Restaurar lógica correta de mediação

**ANTES (correto, segundo BIBLIA):**
```python
if "send_message_to_mediator" in actions:
    return "outros_problemas", "seller_available_action:send_message_to_mediator"
```

**AGORA (quebrado):**
```python
if "send_message_to_mediator" in actions:
    mediation_like = str(claim.get("type") or "").lower() == "mediations" or str(claim.get("stage") or "").lower() == "dispute"
    if mediation_like:
        claim_status = str(claim.get("status") or "").lower()
        stage = str(claim.get("stage") or "").lower()
        if claim_status == "opened" and stage == "dispute" and return_status in {"delivered", "expired", "shipped"}:
            return "outros_problemas", "..."
        return "fora_da_fila", f"mediation_message_to_mediator_not_next_attention:..."
    return "fora_da_fila", f"message_to_mediator_non_mediation:..."
```

**CORREÇÃO:**
- Volta ao caso simples: `send_message_to_mediator` → SEMPRE `outros_problemas`
- Remove as restrições sobre `return_status`, `claim_status`, `stage`
- Mantém o sufixo `_unified` para review (está correto)

#### 1.2 Reverter versão do classificador

```python
ML_CLASSIFIER_VERSION = "actions-v3"  # Volta ao original
```

Isso vai invalidar cache antigo, mas é necessário para recalcular tudo com a regra corrigida.

### FASE 2: Criar validador de conformidade

Novo arquivo: `conformidade_validator.py`

```python
"""
Validador de conformidade com BIBLIA_POS_VENDA_ML.md
Roda antes de cada sync e valida que as regras de classificação estão corretas.
"""

CONFORMIDADE_RULES = {
    "para_revisao": {
        "acao_gatilho": {"return_review_unified_ok", "return_review_unified_fail"},
        "descricao": "Revisao do produto devolvido",
        "source": "BIBLIA_POS_VENDA_ML.md:15-16"
    },
    "para_retirar": {
        "acao_gatilho": {"return_status==label_generated", "reason_id==PDD9967"},
        "descricao": "Retirada em agencia correios",
        "source": "BIBLIA_POS_VENDA_ML.md:18-19"
    },
    "outros_problemas": {
        "acao_gatilho": {"send_message_to_mediator"},
        "descricao": "Mediacao em andamento",
        "source": "BIBLIA_POS_VENDA_ML.md:17"
    }
}

def validate_classify_function_signature():
    """Valida que classify_ml_live_queue_claim respeita as regras."""
    # Implementação
    pass
```

### FASE 3: Congelar regras no código

**Pattern de código imutável:**

```python
# ========== REGRAS CANONICAS - CONGELADAS NA BIBLIA_POS_VENDA_ML.md ==========
# Data de consolidacao: 2026-05-27
# Qualquer mudanca aqui requer:
#   1. Atualizacao da BIBLIA_POS_VENDA_ML.md
#   2. Code review + aprovacao
#   3. Validacao do conformidade_validator
# ============================================================================

def classify_ml_live_queue_claim(claim: dict, return_info: dict) -> tuple[str, str]:
    """
    Classifica claim em bucket segundo BIBLIA_POS_VENDA_ML.md.
    
    Regra 1: review_unified -> para_revisao
    Regra 2: send_message_to_mediator -> outros_problemas
    Regra 3: label_generated + PDD9967 -> para_retirar
    Regra 4: resto -> fora_da_fila
    """
    actions = set(action_names(claim))
    
    # REGRA 1: return_review (BIBLIA:15-16)
    review_actions = {"return_review_unified_ok", "return_review_unified_fail"}
    has_review = bool(actions.intersection(review_actions))
    return_related = return_info.get("related_entities") or []
    already_reviewed = "reviews" in return_related
    if has_review and not already_reviewed:
        return "para_revisao", "seller_available_action:return_review"
    
    # REGRA 2: send_message_to_mediator (BIBLIA:17) - SIMPLES, SEM RESTRICOES
    if "send_message_to_mediator" in actions:
        return "outros_problemas", "seller_available_action:send_message_to_mediator"
    
    # REGRA 3: label_generated + PDD9967 (BIBLIA:18-19)
    return_status = str(return_info.get("status") or "").lower()
    reason = str(claim.get("reason_id") or "")
    if return_status == "label_generated" and reason == "PDD9967":
        return "para_retirar", "return_label_generated_with_pickup_reason"
    
    # REGRA 4: resto (BIBLIA:21)
    return "fora_da_fila", "no_matching_queue_rule"
```

### FASE 4: Documentar mudanças

Atualizar `HANDOFF_CLAUDE.md`:

```markdown
## Regra de Classificacao (actions-v3) - CONGELADA

**IMPORTANTE**: Esta regra é canonizada em BIBLIA_POS_VENDA_ML.md.
Qualquer mudanca requer atualizacao da BIBLIA antes.

| Bucket | Acao gatilho | Condicoes | Ref |
|---|---|---|---|
| para_revisao | return_review_unified_ok/fail | Sem review prévio | BIBLIA:15 |
| outros_problemas | send_message_to_mediator | NENHUMA restricao | BIBLIA:17 |
| para_retirar | label_generated + PDD9967 | reason_id==PDD9967 | BIBLIA:18 |
| fora_da_fila | resto | - | BIBLIA:21 |
```

## Validacao Antes de Commitar

```bash
# 1. Rodar validador
python conformidade_validator.py app.py

# 2. Rodar testes
python -m unittest discover -s tests -v

# 3. Inspecionar cache
python -c "
from app import db
with db() as conn:
    for r in conn.execute('SELECT bucket, COUNT(*) c FROM ml_claim_classifications WHERE active=1 GROUP BY bucket'):
        print(r['bucket'], r['c'])
"

# 4. Comparar com ML dashboard
# Deve bater: para_revisao=X, para_retirar=Y, outros_problemas=Z
```

## Timeline

- **Agora**: Reverter mudanças + criar validador
- **Depois**: Deploy + testar 3 casos reais
- **Após validação**: Congelar regras + documentar

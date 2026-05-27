# Conformidade com BIBLIA_POS_VENDA_ML.md

**Data**: 2026-05-27  
**Status**: ❌ Não Conforme - Alterações do Codex quebraram regras da bíblia

## Problemas Identificados

### 1. Regra de Mediação foi sobre-restringida (CRÍTICO)

**O que a BIBLIA diz:**
```
Bucket: outros_problemas
Ação gatilho: send_message_to_mediator
Comentário: Mediação em andamento
```

**O que o código faz AGORA:**
```python
if "send_message_to_mediator" in actions:
    if mediation_like:
        if claim_status == "opened" and stage == "dispute" and return_status in {"delivered", "expired", "shipped"}:
            return "outros_problemas", "..."  # ✅ Sim
        return "fora_da_fila", f"mediation_message_to_mediator_not_next_attention:..."  # ❌ Não
```

**Impacto:**
- Casos com `send_message_to_mediator` e `return_status=failed` vão para `fora_da_fila`
- Isso QUEBROU os casos reais mencionados:
  - #2000011953595725 (return_status=failed)
  - #2000016110905274 (return_status=failed)

**Causa:**
Mudança não autorizada na lógica. A restrição para apenas `{delivered, expired, shipped}` não aparece na BIBLIA.

### 2. Versão do classificador foi bumped sem necessidade

```python
ML_CLASSIFIER_VERSION = "actions-v23"  # ❌ Era "actions-v3"
```

Isso invalidou TODO o cache anterior sem justificativa na BIBLIA.

### 3. Falta validação de conformidade

Não há nenhum mecanismo que impeça futuras mudanças de quebrarem a BIBLIA.

## Solução

Ver `CONFORMIDADE_IMPLEMENTACAO.md` para as correções.

# Regras Congeladas - Classificação de Filas ML

**Data**: 2026-05-27  
**Status**: CONGELADO - Qualquer alteração requer aprovação + atualização da BIBLIA

## O que está congelado?

A função `classify_ml_live_queue_claim()` em `app.py:1441`

```python
def classify_ml_live_queue_claim(claim: dict, return_info: dict) -> tuple[str, str]:
    # ========== REGRAS CANONICAS - CONGELADAS NA BIBLIA_POS_VENDA_ML.md ==========
```

## As 4 regras canônicas

### REGRA 1: Para Revisão (BIBLIA:15-16)
```
Gatilho: return_review_unified_ok OU return_review_unified_fail
Condições: Produto ainda não foi revisado (not already_reviewed)
Resultado: Bucket = "para_revisao"
```

### REGRA 2: Outros Problemas - MEDIAÇÃO (BIBLIA:17) [🔒 CRÍTICA]
```
Gatilho: send_message_to_mediator
Condições: NENHUMA! Qualquer claim com esta ação VAI para outros_problemas
           Não há restrição por return_status, claim_status, stage, etc
Resultado: Bucket = "outros_problemas"
```

⚠️ **Esta regra foi violada em 2026-05-27 (Codex adicionou restrições)**  
Impacto: Casos #2000011953595725 e #2000016110905274 foram para fora_da_fila

✅ **Restaurada para conformidade**

### REGRA 3: Para Retirar (BIBLIA:18-19)
```
Gatilho: return_status == "label_generated" AND reason_id == "PDD9967"
Condições: Combinadas (label gerado + motivo específico retirada)
Resultado: Bucket = "para_retirar"
```

### REGRA 4: Fora da Fila (BIBLIA:21)
```
Gatilho: resto (nenhuma regra acima se aplica)
Condições: padrão
Resultado: Bucket = "fora_da_fila" com razão "no_matching_queue_rule"
```

## Como Modificar (Protocolo)

**NUNCA mude `classify_ml_live_queue_claim()` sem seguir este protocolo:**

### Passo 1: Atualizar BIBLIA_POS_VENDA_ML.md

Exemplo:
```markdown
## Nova Regra X (Adicionada em 2026-05-27)

Trigger: xyz
Condições: abc
Resultado: bucket = "novo_bucket"
```

### Passo 2: Atualizar a Função

Adicionar a nova lógica **após** ler e validar a BIBLIA.

```python
# REGRA X (BIBLIA:XX): [descrição]
if [trigger] and [conditions]:
    return "[bucket]", "regra_x_reference"
```

### Passo 3: Rodar Validador

```bash
python conformidade_validator.py
```

Deve retornar:
```
[SUCESSO] RESULTADO: CONFORME
```

### Passo 4: Testar com Casos Reais

1. Rodar sync: `POST /api/devolucoes/sincronizar-ml`
2. Validar contagens batem com ML dashboard
3. Testar 3+ casos reais no bucket novo

### Passo 5: Code Review

**Checklist obrigatório:**
- [ ] BIBLIA_POS_VENDA_ML.md foi atualizado?
- [ ] conformidade_validator.py passou?
- [ ] 3+ casos reais foram testados?
- [ ] Contagens batem com ML?

## O que NÃO mude

### ❌ Adicionar "restrições invisíveis"

```python
# ERRADO - Restrição escondida
if "send_message_to_mediator" in actions:
    if return_status in {"delivered", "shipped"}:  # ❌ Nenhuma autorização para isso
        return "outros_problemas", ...
    return "fora_da_fila", ...
```

```python
# CERTO - Sem restrições
if "send_message_to_mediator" in actions:
    return "outros_problemas", ...  # ✅ SEMPRE
```

### ❌ Mudar a versão do classificador sem documentar

```python
# ERRADO
ML_CLASSIFIER_VERSION = "actions-v25"  # Bumped sem documentação
```

```python
# CERTO (quando documentado na BIBLIA)
ML_CLASSIFIER_VERSION = "actions-v4"  # Adicionada REGRA X em BIBLIA:XX
```

### ❌ "Casos especiais" que não estão na BIBLIA

Se disser "mas esse caso específico..." → deve estar documentado na BIBLIA.

## Validação Automática

O `conformidade_validator.py` vai detectar:

- ✅ Restrições não-autorizadas
- ✅ Versão de classificador bumped
- ✅ Lógica extra não-documentada

```bash
$ python conformidade_validator.py

[CRITICA] Se detectar violações:
[FALHA] VIOLACOES DETECTADAS (nao-conformes com BIBLIA)
```

## Histórico de Mudanças

| Data | O quê | Por quê | Aprovador |
|------|-------|---------|-----------|
| 2026-05-27 | Restaurada REGRA 2 de restrições | Violação crítica detectada | claude-code |
| 2026-05-27 | Revertida v23 → v3 | Versão bumped sem autorização | claude-code |
|  |  |  |  |

## Próximos Passos Recomendados

1. **Monitorar ML dashboard** por 7 dias após restore
2. **Alertar se** contagens de buckets ficarem inconsistentes
3. **Revisar logs** de sync para garantir REGRA 2 sendo aplicada corretamente
4. **Documentar** se houver novos padrões que precisem de regras novas

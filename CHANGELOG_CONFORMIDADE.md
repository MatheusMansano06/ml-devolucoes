# Changelog - Conformidade com BIBLIA_POS_VENDA_ML.md

## 2026-05-27 - RESTAURACAO DE CONFORMIDADE (CRITICAL FIX)

### Status
**ANTES**: ❌ Não-conforme - 3 violações críticas detectadas  
**DEPOIS**: ✅ Conforme - 100% alinhado com BIBLIA_POS_VENDA_ML.md

### O que foi quebrado (pelo Codex)

#### 1. REGRA 2 sobre-restringida (CRÍTICO)

**Problema**: Casos com `send_message_to_mediator` estavam sendo enviados para `fora_da_fila` ao invés de `outros_problemas`

**Impacto Real**:
- #2000011953595725 (return_status=failed) → fora_da_fila ❌
- #2000016110905274 (return_status=failed) → fora_da_fila ❌
- Estes deveriam estar em **outros_problemas** conforme BIBLIA:17

**Causa**:
```python
# ANTES (ERRADO - violava BIBLIA:17)
if "send_message_to_mediator" in actions:
    if mediation_like:
        if claim_status == "opened" and stage == "dispute" and return_status in {"delivered", "expired", "shipped"}:
            return "outros_problemas", ...  # Só SIM se retorno_status for specific
        return "fora_da_fila", ...  # Não! Viola BIBLIA

# DEPOIS (CORRETO - segue BIBLIA:17)
if "send_message_to_mediator" in actions:
    return "outros_problemas", ...  # SIM, sempre! NENHUMA RESTRIÇÃO
```

#### 2. Versão do classificador bumped indevidamente

```python
# ANTES (ERRADO)
ML_CLASSIFIER_VERSION = "actions-v23"  # Invalidou cache inteiro

# DEPOIS (CORRETO)
ML_CLASSIFIER_VERSION = "actions-v3"   # Original, canonizado na BIBLIA
```

### Arquivos criados para evitar futuras quebras

1. **conformidade_validator.py** - Valida que código respeita BIBLIA
   - Roda em pre-commit
   - Detecta restrições não-autorizadas
   - Valida versão do classificador

2. **CONFORMIDADE_BIBLIA.md** - Documenta problema + solução

3. **CONFORMIDADE_IMPLEMENTACAO.md** - Guia de implementação segura

4. **CHANGELOG_CONFORMIDADE.md** (este arquivo) - Histórico de correções

### Regras Canônicas (agora protegidas)

```
Para Revisao (BIBLIA:15-16):
  - Gatilho: return_review_unified_ok OU return_review_unified_fail
  - Condicoes: Sem review prévio

Outros Problemas (BIBLIA:17) [PROTEGIDO]:
  - Gatilho: send_message_to_mediator
  - Condicoes: NENHUMA RESTRICAO - qualquer claim com esta ação vai aqui

Para Retirar (BIBLIA:18-19):
  - Gatilho: return_status==label_generated AND reason_id==PDD9967
  - Condicoes: Combinadas

Fora da Fila (BIBLIA:21):
  - Gatilho: resto (nenhuma regra acima)
  - Condicoes: padrão
```

### Como evitar no futuro

Antes de alterar `classify_ml_live_queue_claim`:

1. **Atualizar BIBLIA_POS_VENDA_ML.md** primeiro
2. **Rodar validador**:
   ```bash
   python conformidade_validator.py
   ```
3. **Code review** com foco em BIBLIA
4. **Validar 3+ casos reais** no ML dashboard

### Teste de validação

```bash
$ python conformidade_validator.py
[INFO] Validando conformidade...
[OK] Funcao encontrada
[OK] REGRA 2: Nenhuma restricao detectada
[OK] Versao correta: actions-v3
[SUCESSO] RESULTADO: CONFORME
```

### Cache invalidado

Todo cache foi invalidado (classifier_version mudou de v23 → v3).
Próximo sync vai recalcular tudo com regras corretas.

**Tempo esperado**: 20-30s para repopular cache (23 claims × 3 chamadas em 4 workers)

---

**Verificado por**: conformidade_validator.py  
**Data de conformidade**: 2026-05-27  
**Pr

#!/usr/bin/env python3
"""
Validador de Conformidade com BIBLIA_POS_VENDA_ML.md

Este script valida que o classificador em app.py respeita as regras
canonizadas na BIBLIA. Deve ser rodado antes de cada deploy.

Uso:
    python conformidade_validator.py
"""

import ast
import sys
from pathlib import Path

# Regras canonizadas (extraídas da BIBLIA_POS_VENDA_ML.md em 2026-05-27)
BIBLIA_RULES = {
    "para_revisao": {
        "trigger": {"return_review_unified_ok", "return_review_unified_fail"},
        "conditions": "sem review prévio",
        "line_ref": "15-16"
    },
    "outros_problemas": {
        "trigger": {"send_message_to_mediator"},
        "conditions": "NENHUMA RESTRIÇÃO - qualquer claim com esta ação vai aqui",
        "line_ref": "17",
        "CRITICAL": True
    },
    "para_retirar": {
        "trigger": "return_status==label_generated AND reason_id==PDD9967",
        "conditions": "condição combinada",
        "line_ref": "18-19"
    },
    "fora_da_fila": {
        "trigger": "resto (nenhuma regra acima aplica)",
        "conditions": "padrão",
        "line_ref": "21"
    }
}


def load_app_py():
    """Carrega app.py como AST."""
    app_path = Path(__file__).parent / "app.py"
    with open(app_path) as f:
        return ast.parse(f.read()), str(app_path)


def find_classify_function(tree):
    """Encontra a função classify_ml_live_queue_claim no AST."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "classify_ml_live_queue_claim":
            return node
    return None


def check_rule_2_conformance(func_node):
    """
    Valida REGRA 2 (CRÍTICA): send_message_to_mediator -> outros_problemas

    A regra não pode ter restrições como:
    - if mediation_like: ...else: fora_da_fila
    - if claim_status == "opened": ...else: fora_da_fila
    - if return_status in {...}: ...else: fora_da_fila

    Deve ser simples:
    if "send_message_to_mediator" in actions:
        return "outros_problemas", ...
    """
    source = ast.get_source_segment(open(Path(__file__).parent / "app.py").read(), func_node)

    # Red flags que indicam restrições não autorizadas
    red_flags = [
        'if mediation_like:',
        'if claim_status ==',
        'if stage ==',
        'if return_status in',
        'fora_da_fila.*mediation_message_to_mediator_not_next_attention',
        'message_to_mediator_non_mediation',
    ]

    violations = []
    for i, line in enumerate(source.split('\n'), 1):
        for flag in red_flags:
            if flag.lower() in line.lower():
                violations.append({
                    'line': i,
                    'content': line.strip(),
                    'issue': f'Restrição não autorizada detectada: {flag}'
                })

    return violations


def validate():
    """Executa todas as validações."""
    print("[INFO] Validando conformidade com BIBLIA_POS_VENDA_ML.md...")
    print()

    tree, app_path = load_app_py()
    classify_func = find_classify_function(tree)

    if not classify_func:
        print("[ERRO] Funcao classify_ml_live_queue_claim nao encontrada em app.py")
        return False

    print("[OK] Funcao classify_ml_live_queue_claim encontrada")
    print()

    # VALIDACAO 1: REGRA 2 (CRITICA)
    print("=" * 70)
    print("[CRITICA] VALIDACAO: REGRA 2 (send_message_to_mediator)")
    print("=" * 70)
    print(BIBLIA_RULES["outros_problemas"]["conditions"])
    print()

    violations = check_rule_2_conformance(classify_func)

    if violations:
        print("[FALHA] VIOLACOES DETECTADAS (nao-conformes com BIBLIA):")
        print()
        for v in violations:
            print(f"  Linha {v['line']}: {v['content']}")
            print(f"  -> {v['issue']}")
            print()
        print("[AVISO] A BIBLIA diz: NENHUMA RESTRICAO")
        print("        Qualquer claim com send_message_to_mediator DEVE ir para outros_problemas")
        print()
        return False
    else:
        print("[OK] REGRA 2: Nenhuma restricao nao-autorizada detectada")
        print()

    # VALIDACAO 2: Versao do classificador
    print("=" * 70)
    print("[INFO] VALIDACAO: Versao do classificador")
    print("=" * 70)

    with open(Path(__file__).parent / "app.py") as f:
        content = f.read()
        if 'ML_CLASSIFIER_VERSION = "actions-v23"' in content:
            print("[AVISO] Versao bumped para actions-v23")
            print("        Antes: actions-v3 (original)")
            print("        Isso invalida TODO o cache")
            print("        Justificativa necessaria na BIBLIA_POS_VENDA_ML.md")
            return False
        elif 'ML_CLASSIFIER_VERSION = "actions-v3"' in content:
            print("[OK] Versao correta: actions-v3 (original da BIBLIA)")

    print()
    print("=" * 70)
    print("[SUCESSO] RESULTADO: CONFORME")
    print("=" * 70)
    return True


if __name__ == "__main__":
    success = validate()
    sys.exit(0 if success else 1)

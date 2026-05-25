#!/usr/bin/env python3
"""
Debug da sincronização com Mercado Livre
Mostra EXATAMENTE quais pedidos são sincronizados e em qual categoria caem
"""

import os
import sqlite3
from pathlib import Path
from dotenv import dotenv_values
from concurrent.futures import ThreadPoolExecutor, as_completed

# Importar funções do app
import sys
sys.path.insert(0, str(Path(__file__).parent))
from app import (
    ml_get, ml_access_token, review_due_date, claim_return_status,
    opened_claims_for_next_attendance, build_ml_devolucao, current_env, now_iso
)

def debug_sincronizacao():
    """Debug completo da sincronização"""
    env_values = current_env()
    user_id = env_values.get("ML_USER_ID", "")

    if not user_id:
        print("❌ ML_USER_ID não configurado")
        return

    print("=" * 80)
    print("🔍 DEBUG: SINCRONIZAÇÃO COM MERCADO LIVRE")
    print("=" * 80)
    print(f"\n📊 User ID: {user_id}")
    print(f"Buscando pedidos de devolução...")

    # Buscar todas as claims
    claims_abertas = []
    claims_fechadas = []

    for status_filter in ("opened", "closed"):
        print(f"\n📥 Buscando status '{status_filter}'...")
        max_offset = 1000 if status_filter == "opened" else 1200

        for offset in range(0, max_offset, 100):
            try:
                data = ml_get(
                    "/post-purchase/v1/claims/search",
                    {
                        "user_id": user_id,
                        "status": status_filter,
                        "limit": 100,
                        "offset": offset,
                        "sort": "date_desc"
                    },
                )
                batch = data.get("data") or data.get("results") or []
                if not batch:
                    print(f"  Offset {offset}: Nenhum resultado")
                    break

                print(f"  Offset {offset}: {len(batch)} pedidos encontrados")
                if status_filter == "opened":
                    claims_abertas.extend(batch)
                else:
                    claims_fechadas.extend(batch)

                if len(batch) < 100:
                    break
            except Exception as e:
                print(f"  ❌ Erro no offset {offset}: {e}")
                break

    print(f"\n✅ Total de claims abertas: {len(claims_abertas)}")
    print(f"✅ Total de claims fechadas: {len(claims_fechadas)}")
    print(f"✅ TOTAL: {len(claims_abertas) + len(claims_fechadas)}")

    # Processar apenas as abertas (como a função faz)
    print("\n" + "=" * 80)
    print("🔄 PROCESSANDO CLAIMS ABERTAS")
    print("=" * 80)

    revisao_list = []
    retirar_list = []
    outros_list = []
    ignoradas = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(build_ml_devolucao, claim): claim for claim in claims_abertas}

        for future in as_completed(futures):
            claim = futures[future]
            claim_id = claim.get("id")
            try:
                item = future.result()
                prioridade = item.get("prioridade_prazo", "")
                status = item.get("status", "")
                acao = item.get("acao_recomendada", "")

                # Classificar
                if prioridade == "hoje" and status in {"produto_recebido", "divergencia_encontrada"}:
                    revisao_list.append({
                        "id": claim_id,
                        "prioridade": prioridade,
                        "status": status,
                        "acao": acao
                    })
                elif prioridade == "retirar_correio":
                    retirar_list.append({
                        "id": claim_id,
                        "prioridade": prioridade,
                        "status": status,
                        "acao": acao
                    })
                else:
                    outros_list.append({
                        "id": claim_id,
                        "prioridade": prioridade,
                        "status": status,
                        "acao": acao
                    })
            except Exception as e:
                ignoradas.append({
                    "id": claim_id,
                    "erro": str(e)
                })

    # Exibir resultados
    print("\n" + "=" * 80)
    print(f"📋 PARA SUA REVISÃO ({len(revisao_list)} pedidos)")
    print("=" * 80)
    for item in revisao_list[:10]:  # Mostrar primeiros 10
        print(f"  #{item['id']}: {item['status']} - {item['acao']}")
    if len(revisao_list) > 10:
        print(f"  ... e mais {len(revisao_list) - 10}")

    print("\n" + "=" * 80)
    print(f"🚚 PARA RETIRAR NO CORREIO ({len(retirar_list)} pedidos)")
    print("=" * 80)
    for item in retirar_list[:10]:
        print(f"  #{item['id']}: {item['status']} - {item['acao']}")
    if len(retirar_list) > 10:
        print(f"  ... e mais {len(retirar_list) - 10}")

    print("\n" + "=" * 80)
    print(f"⚠️  OUTROS PROBLEMAS ({len(outros_list)} pedidos)")
    print("=" * 80)
    for item in outros_list[:10]:
        print(f"  #{item['id']}: {item['status']} - {item['acao']}")
    if len(outros_list) > 10:
        print(f"  ... e mais {len(outros_list) - 10}")

    if ignoradas:
        print("\n" + "=" * 80)
        print(f"❌ IGNORADAS ({len(ignoradas)} pedidos)")
        print("=" * 80)
        for item in ignoradas[:5]:
            print(f"  #{item['id']}: {item['erro']}")
        if len(ignoradas) > 5:
            print(f"  ... e mais {len(ignoradas) - 5}")

    # Resumo final
    print("\n" + "=" * 80)
    print("📊 RESUMO FINAL")
    print("=" * 80)
    total_sincronizado = len(revisao_list) + len(retirar_list) + len(outros_list)
    print(f"Para sua revisão:        {len(revisao_list):>3} pedidos")
    print(f"Para retirar no correio: {len(retirar_list):>3} pedidos")
    print(f"Outros problemas:        {len(outros_list):>3} pedidos")
    print(f"{'─' * 40}")
    print(f"TOTAL SINCRONIZADO:      {total_sincronizado:>3} pedidos")
    print(f"IGNORADOS:               {len(ignoradas):>3} pedidos")
    print(f"ML TOTAL:                {len(claims_abertas):>3} pedidos (abertas)")
    print("=" * 80)

if __name__ == "__main__":
    try:
        debug_sincronizacao()
    except Exception as e:
        print(f"❌ Erro: {e}")
        import traceback
        traceback.print_exc()

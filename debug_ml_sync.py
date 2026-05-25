#!/usr/bin/env python3
import os, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))
from app import ml_get, build_ml_devolucao, current_env, ml_access_token

env_values = current_env()
user_id = env_values.get("ML_USER_ID", "")

print("DEBUG: Sincronizacao ML\n")
print(f"Config: CLIENT_ID={bool(env_values.get('ML_CLIENT_ID'))}, USER_ID={user_id}\n")

try:
    token = ml_access_token()
    print("[1] Token OK\n")
    
    data = ml_get("/post-purchase/v1/claims/search", {"user_id": user_id, "status": "opened", "type": "returns", "limit": 100})
    claims = data.get("data") or data.get("results") or []
    print(f"[2] Claims encontradas: {len(claims)}\n")
    
    if claims:
        print(f"[3] Processando claims...")
        devolucoes = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(build_ml_devolucao, c) for c in claims]
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    d = future.result()
                    devolucoes.append(d)
                    print(f"  {i}. Pedido {d['pedido_id']}: {d['prioridade_prazo']}")
                except Exception as e:
                    print(f"  {i}. ERRO: {e}")
        
        print(f"\n[4] Resultado: {len(devolucoes)} processadas")
        revisao = sum(1 for d in devolucoes if d['status'] in {'produto_recebido', 'divergencia_encontrada', 'em_analise'} and d['requer_acao'] == 1)
        retirar = sum(1 for d in devolucoes if d['ml_tipo_logistica'] != 'full_ml' and d['requer_acao'] == 1)
        print(f"    Para revisao: {revisao}\n    Para retirar: {retirar}")
    else:
        print("[AVISO] Nenhuma devolucao encontrada!")
        
except Exception as e:
    print(f"[ERRO] {e}")
    import traceback
    traceback.print_exc()

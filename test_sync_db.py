import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from app import ml_get, build_ml_devolucao, upsert_ml_devolucao, current_env, db
from concurrent.futures import ThreadPoolExecutor, as_completed

env_values = current_env()
user_id = env_values.get("ML_USER_ID")

data = ml_get("/post-purchase/v1/claims/search", 
    {"user_id": user_id, "status": "opened", "type": "returns", "limit": 100})
claims = data.get("data") or data.get("results") or []

print(f"Sincronizando {len(claims)} devoluções...\n")

created = updated = 0
with ThreadPoolExecutor(max_workers=8) as executor:
    futures = [executor.submit(build_ml_devolucao, claim) for claim in claims]
    for i, future in enumerate(as_completed(futures), 1):
        try:
            dev = future.result()
            action = upsert_ml_devolucao(dev)
            if action == "created":
                created += 1
            else:
                updated += 1
            print(f"[{i}/{len(claims)}] {dev['pedido_id']}: {action}")
        except Exception as e:
            print(f"[{i}/{len(claims)}] ERRO: {e}")

print(f"\nResultado:")
print(f"  Criadas: {created}")
print(f"  Atualizadas: {updated}")
print(f"  Total: {created + updated}")

# Verificar no banco
with db() as conn:
    total_db = conn.execute("SELECT COUNT(*) as count FROM devolucoes").fetchone()["count"]
    por_status = conn.execute("""
        SELECT status, COUNT(*) as count FROM devolucoes GROUP BY status ORDER BY count DESC
    """).fetchall()
    
print(f"\nBanco de dados:")
print(f"  Total: {total_db}")
print(f"  Por status:")
for row in por_status:
    print(f"    - {row['status']}: {row['count']}")

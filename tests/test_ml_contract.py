import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import app


class MercadoLivreContractTests(unittest.TestCase):
    def test_claims_search_uses_returns_type_by_default(self):
        calls = []

        def fake_ml_get(path, params):
            calls.append((path, params))
            return {"paging": {"total": 0}, "data": []}

        with patch.object(app, "ml_get", side_effect=fake_ml_get):
            claims, total = app.ml_claims_search("123", "opened", max_pages=1)

        self.assertEqual(claims, [])
        self.assertEqual(total, 0)
        self.assertEqual(calls[0][0], "/post-purchase/v1/claims/search")
        self.assertEqual(calls[0][1]["type"], "returns")

    def test_return_shipments_accepts_current_and_legacy_shapes(self):
        self.assertEqual(app.ml_return_shipments({"shipments": [{"id": "s1"}]})[0]["id"], "s1")
        self.assertEqual(app.ml_return_shipments({"shipping": {"id": "s2"}})[0]["id"], "s2")
        self.assertEqual(app.ml_return_shipments({"shipment": {"id": "s3"}})[0]["id"], "s3")
        self.assertEqual(app.ml_return_shipments({}), [{}])

    def test_classify_ml_next_claim_sets_expected_bucket(self):
        claim = {"id": "c1", "status": "opened", "reason_id": "PDD9967"}
        kind = app.classify_ml_next_claim(claim, {"status": "label_generated"})
        self.assertEqual(kind, "retirar_correio")
        self.assertEqual(claim["_next_kind"], "retirar_correio")

        claim = {
            "id": "c2",
            "status": "opened",
            "reason_id": "PDD9944",
            "players": [{"type": "seller", "available_actions": [{"action": "return_review_unified_ok"}]}],
        }
        kind = app.classify_ml_next_claim(claim, {"status": "shipped"})
        self.assertEqual(kind, "revisao")
        self.assertEqual(claim["_next_kind"], "revisao")

        claim = {
            "id": "c6",
            "status": "closed",
            "reason_id": "PDD9939",
            "players": [{"type": "seller", "available_actions": [{"action": "return_review_unified_ok"}]}],
        }
        kind = app.classify_ml_next_claim(claim, {"status": "delivered", "shipment_status": "delivered"})
        self.assertEqual(kind, "revisao")
        self.assertEqual(claim["_next_kind"], "revisao")

        claim = {"id": "c5", "status": "opened", "stage": "dispute", "type": "mediations", "reason_id": "PDD9949"}
        self.assertIsNone(app.classify_ml_next_claim(claim, {"status": "delivered", "shipment_status": "delivered"}))

        claim = {
            "id": "c7",
            "status": "opened",
            "stage": "dispute",
            "type": "mediations",
            "reason_id": "PDD9939",
            "players": [{"type": "seller", "available_actions": [{"action": "send_message_to_mediator"}]}],
        }
        bucket, rule = app.classify_ml_live_queue_claim(claim, {"status": "delivered", "shipment_status": "delivered"})
        self.assertEqual(bucket, "outros_problemas")
        self.assertEqual(rule, "seller_available_action:send_message_to_mediator")

        claim = {"id": "c4", "status": "opened", "reason_id": "PDD9944"}
        kind = app.classify_ml_next_claim(claim, {"status": "label_generated", "shipment_status": "ready_to_ship"})
        self.assertEqual(kind, "outros_problemas")

        claim = {"id": "c3", "status": "closed"}
        self.assertIsNone(app.classify_ml_next_claim(claim, {"status": "delivered"}))

    def test_raw_payload_and_sync_run_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(app, "DB_PATH", Path(tmpdir) / "test.sqlite"):
            app.init_database()
            run_id = app.start_ml_sync_run("test", {"source": "unit"})
            app.save_ml_raw_payload(run_id, "claim", "c1", {"id": "c1", "status": "opened"}, "c1")
            app.add_ml_reconciliation_diff(run_id, "total", "warning", "c1", "detail")
            app.finish_ml_sync_run(
                run_id,
                status="partial",
                total_declarado=2,
                total_encontrado=1,
                total_processado=1,
                total_erros=1,
            )

            with app.db() as conn:
                run = conn.execute("SELECT * FROM ml_sync_runs WHERE id = ?", [run_id]).fetchone()
                raw = conn.execute("SELECT * FROM ml_raw_payloads WHERE resource_id = 'c1'").fetchone()
                diff = conn.execute("SELECT * FROM ml_reconciliation_diffs WHERE sync_run_id = ?", [run_id]).fetchone()

            self.assertEqual(run["status"], "partial")
            self.assertEqual(raw["claim_id"], "c1")
            self.assertEqual(diff["severidade"], "warning")

    def test_classification_cache_persists_enrichment_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(app, "DB_PATH", Path(tmpdir) / "test.sqlite"):
            app.init_database()
            item = {
                "claim_id": "claim-enriched",
                "pedido_id": "2000000000000001",
                "order_ids": ["2000000000000002"],
                "status": "opened",
                "stage": "claim",
                "type": "returns",
                "reason_id": "PDD9944",
                "return_id": "ret-1",
                "return_status": "delivered",
                "shipment_status": "delivered",
                "shipment_destination": "seller_address",
                "seller_actions": ["return_review_unified_ok"],
                "bucket": "para_revisao",
                "regra": "seller_available_action:return_review",
                "date_created": "2026-05-20T10:00:00Z",
                "last_updated": "2026-05-25T10:00:00Z",
                "classifier_version": app.ML_CLASSIFIER_VERSION,
                "enrichment_version": app.ML_ENRICHMENT_VERSION,
                "produto_nome": "Capa de Banco",
                "produto_imagem": "https://example.com/img.jpg",
                "valor_pago": 99.9,
                "taxa_venda": 12.5,
                "ml_tipo_logistica": "full_ml",
                "pack_id": "pack-1",
                "motivo_label": "Produto danificado",
                "mandatory": 1,
                "due_date": "2026-05-30T18:00:00Z",
            }
            app.save_claim_classification(item)
            with app.db() as conn:
                row = conn.execute("SELECT * FROM ml_claim_classifications WHERE claim_id = ?", [item["claim_id"]]).fetchone()
            self.assertEqual(row["produto_nome"], "Capa de Banco")
            self.assertEqual(row["bucket"], "para_revisao")
            self.assertAlmostEqual(row["valor_pago"], 99.9)
            self.assertEqual(row["ml_tipo_logistica"], "full_ml")
            self.assertEqual(row["motivo_label"], "Produto danificado")
            self.assertEqual(row["mandatory"], 1)
            self.assertEqual(row["due_date"], "2026-05-30T18:00:00Z")

            cached = app.cached_claim_classification(item["claim_id"], item["last_updated"])
            self.assertIsNotNone(cached)
            self.assertEqual(cached["payload"]["produto_nome"], "Capa de Banco")

    def test_cards_endpoint_returns_only_active_rows_for_bucket(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(app, "DB_PATH", Path(tmpdir) / "test.sqlite"):
            app.init_database()
            base = {
                "order_ids": [],
                "status": "opened",
                "stage": "claim",
                "type": "returns",
                "return_id": "",
                "return_status": "",
                "shipment_status": "",
                "shipment_destination": "",
                "seller_actions": [],
                "regra": "",
                "date_created": "",
                "last_updated": "",
                "classifier_version": app.ML_CLASSIFIER_VERSION,
                "enrichment_version": app.ML_ENRICHMENT_VERSION,
                "produto_imagem": "",
                "valor_pago": 0,
                "taxa_venda": 0,
                "ml_tipo_logistica": "",
                "pack_id": "",
                "mandatory": 0,
                "due_date": "",
            }
            app.save_claim_classification({**base, "claim_id": "c-rev-1", "pedido_id": "p1", "reason_id": "PDD9944", "bucket": "para_revisao", "produto_nome": "Item Revisao", "motivo_label": "Produto danificado"})
            app.save_claim_classification({**base, "claim_id": "c-out-1", "pedido_id": "p2", "reason_id": "PDD9939", "bucket": "outros_problemas", "produto_nome": "Item Outros", "motivo_label": "Arrependeu"})
            with app.db() as conn:
                conn.execute("UPDATE ml_claim_classifications SET active = 0 WHERE claim_id = 'c-out-1'")

            with app.app.test_client() as client:
                with client.session_transaction() as session:
                    session["logged_in"] = True
                response = client.get("/api/devolucoes/cards?bucket=para_revisao")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["bucket"], "para_revisao")
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["cards"][0]["claim_id"], "c-rev-1")
            self.assertEqual(data["cards"][0]["produto_nome"], "Item Revisao")

            with app.app.test_client() as client:
                with client.session_transaction() as session:
                    session["logged_in"] = True
                bad = client.get("/api/devolucoes/cards?bucket=qualquer")
            self.assertEqual(bad.status_code, 400)

    def test_bucket_action_meta_extracts_mandatory_and_due_date(self):
        claim = {
            "players": [
                {"type": "respondent", "available_actions": [
                    {"action": "return_review_unified_ok", "mandatory": True, "due_date": "2026-05-30T18:00:00Z"},
                    {"action": "send_message_to_mediator", "mandatory": False, "due_date": "2026-06-01T18:00:00Z"},
                ]},
            ],
        }
        meta = app.bucket_action_meta(claim, "para_revisao")
        self.assertEqual(meta["mandatory"], 1)
        self.assertEqual(meta["due_date"], "2026-05-30T18:00:00Z")

        meta = app.bucket_action_meta(claim, "outros_problemas")
        self.assertEqual(meta["mandatory"], 0)
        self.assertEqual(meta["due_date"], "2026-06-01T18:00:00Z")

    def test_apply_ml_queue_window_restores_previously_demoted_items(self):
        rows = [
            {"claim_id": "c1", "bucket": "outros_problemas", "regra": "seller_available_action:send_message_to_mediator", "last_updated": "2026-05-26T10:00:00Z"},
            {"claim_id": "c2", "bucket": "fora_da_fila", "regra": "seller_available_action:send_message_to_mediator:outside_recent_window", "last_updated": "2026-05-26T09:00:00Z"},
            {"claim_id": "c3", "bucket": "fora_da_fila", "regra": "seller_available_action:send_message_to_mediator:outside_recent_window", "last_updated": "2026-05-26T08:00:00Z"},
        ]
        with patch.object(app, "env_int", return_value=10):
            app.apply_ml_queue_window(rows)
        buckets = {row["claim_id"]: row["bucket"] for row in rows}
        self.assertEqual(buckets, {"c1": "outros_problemas", "c2": "outros_problemas", "c3": "outros_problemas"})
        for row in rows:
            self.assertNotIn(":outside_recent_window", row["regra"])

        rows = [
            {"claim_id": f"c{i}", "bucket": "outros_problemas", "regra": "seller_available_action:send_message_to_mediator", "last_updated": f"2026-05-{10+i:02d}T10:00:00Z"}
            for i in range(5)
        ]
        with patch.object(app, "env_int", return_value=3):
            app.apply_ml_queue_window(rows)
        active = [r for r in rows if r["bucket"] == "outros_problemas"]
        demoted = [r for r in rows if r["bucket"] == "fora_da_fila"]
        self.assertEqual(len(active), 3)
        self.assertEqual(len(demoted), 2)
        active_ids = {r["claim_id"] for r in active}
        self.assertEqual(active_ids, {"c2", "c3", "c4"})

    def test_upsert_does_not_merge_different_claims_with_same_order(self):
        base = {
            "marketplace": "Mercado Livre",
            "pedido_id": "order-1",
            "cliente_nome": "Cliente",
            "produto_nome": "Produto",
            "motivo_devolucao": "Motivo",
            "valor_produto": 10,
            "status": "produto_recebido",
            "data_solicitacao": "2026-01-01T00:00:00+00:00",
            "codigo_rastreio": "track",
            "valor_recuperado": 0,
            "valor_perdido": 0,
            "observacao_final": "",
            "ml_status": "opened",
            "ml_stage": "",
            "ml_return_status": "delivered",
            "ml_destino_devolucao": "",
            "ml_tipo_logistica": "seller_address",
            "prazo_resolucao": None,
            "prioridade_prazo": "hoje",
            "requer_acao": 1,
            "acao_recomendada": "",
            "produto_imagem": "",
            "chegada_status": "",
            "mediacao_mensagem": "",
            "ml_ativo": 1,
            "ml_valor_pago": 10,
            "ml_valor_reembolsado": 0,
            "ml_taxa_venda": 0,
            "ml_custo_envio": 0,
            "ml_status_pagamento": "",
            "ml_return_id": "",
            "ml_return_subtype": "",
            "ml_status_money": "",
            "ml_refund_at": "",
            "ml_seller_status": "",
            "ml_seller_reason": "",
            "ml_product_condition": "",
            "ml_return_reviews": "[]",
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(app, "DB_PATH", Path(tmpdir) / "test.sqlite"):
            app.init_database()
            app.upsert_ml_devolucao({**base, "ml_claim_id": "claim-1"})
            app.upsert_ml_devolucao({**base, "ml_claim_id": "claim-2"})
            with app.db() as conn:
                total = conn.execute("SELECT COUNT(*) AS total FROM devolucoes WHERE pedido_id = 'order-1'").fetchone()["total"]
            self.assertEqual(total, 2)


if __name__ == "__main__":
    unittest.main()

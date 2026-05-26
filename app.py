from __future__ import annotations

import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from urllib.parse import urlencode
from uuid import uuid4

import requests
from dotenv import dotenv_values
from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename


ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"
DB_PATH = ROOT_DIR / "data" / "devolucoes.sqlite"
UPLOAD_DIR = ROOT_DIR / "uploads"
ML_CLASSIFIER_VERSION = "actions-v17"
ML_CLOSED_TOUCH_GAP_HOURS = 24


def claim_benefited_complainant_only(claim: dict) -> bool:
    resolution = claim.get("resolution") or {}
    benefited = resolution.get("benefited") or []
    return list(benefited) == ["complainant"]


def claim_touched_after_resolution(claim: dict) -> bool:
    resolution = claim.get("resolution") or {}
    res_date = parse_ml_datetime(resolution.get("date_created"))
    last_updated = parse_ml_datetime(claim.get("last_updated"))
    if not res_date or not last_updated:
        return False
    gap = last_updated - res_date
    return gap >= timedelta(hours=ML_CLOSED_TOUCH_GAP_HOURS)
ML_ENRICHMENT_VERSION = "enrich-v1"

MOTIVO_LABELS = {
    "PDD9939": "O comprador se arrependeu",
    "PDD9949": "O produto nao funciona",
    "PDD9967": "Para retirar no correio",
    "PDD9968": "Produto diferente",
    "PDD9941": "Acessorio faltando",
    "PDD9942": "Produto incompleto",
    "PDD9944": "Produto danificado",
    "PDD9946": "A embalagem chegou danificada",
    "PDD9952": "Afetou a reputacao",
}


def motivo_label(reason_id: str | None) -> str:
    return MOTIVO_LABELS.get(str(reason_id or ""), str(reason_id or "") or "-")

STATUS_PERMITIDOS = {
    "aguardando_produto",
    "em_transito",
    "nao_recebido",
    "produto_recebido",
    "em_analise",
    "divergencia_encontrada",
    "sem_divergencia",
    "contestacao_aberta",
    "aguardando_plataforma",
    "aprovado",
    "parcial",
    "reprovado",
    "encerrado",
    "nao_recebido",
}

env = {**os.environ, **dotenv_values(ENV_PATH)}

app = Flask(__name__)
app.secret_key = env.get("FLASK_SECRET_KEY", "ml-devolucoes-local-dev")

STORE_URL = env.get("STORE_URL", "https://lista.mercadolivre.com.br/loja/novaes-moto-pecas/")
PIN_MERCADO_LIVRE = env.get("PIN_MERCADO_LIVRE", "1234")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def init_database() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS devolucoes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              marketplace TEXT NOT NULL,
              pedido_id TEXT NOT NULL,
              cliente_nome TEXT NOT NULL,
              produto_nome TEXT NOT NULL,
              motivo_devolucao TEXT NOT NULL,
              valor_produto REAL NOT NULL,
              status TEXT NOT NULL,
              data_solicitacao TEXT,
              codigo_rastreio TEXT,
              valor_recuperado REAL DEFAULT 0,
              valor_perdido REAL DEFAULT 0,
              observacao_final TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS historico_status (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              devolucao_id INTEGER NOT NULL,
              status_anterior TEXT NOT NULL,
              status_novo TEXT NOT NULL,
              data_alteracao TEXT NOT NULL,
              FOREIGN KEY (devolucao_id) REFERENCES devolucoes(id)
            );

            CREATE TABLE IF NOT EXISTS checklists (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              devolucao_id INTEGER NOT NULL UNIQUE,
              produto_confere INTEGER,
              embalagem_integra INTEGER,
              possui_sinais_de_uso INTEGER,
              item_quebrado INTEGER,
              faltando_pecas INTEGER,
              motivo_confere INTEGER,
              observacoes TEXT DEFAULT '',
              data_checklist TEXT NOT NULL,
              FOREIGN KEY (devolucao_id) REFERENCES devolucoes(id)
            );

            CREATE TABLE IF NOT EXISTS evidencias (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              devolucao_id INTEGER NOT NULL,
              tipo TEXT NOT NULL,
              arquivo TEXT NOT NULL,
              descricao TEXT DEFAULT '',
              data_upload TEXT NOT NULL,
              FOREIGN KEY (devolucao_id) REFERENCES devolucoes(id)
            );

            CREATE TABLE IF NOT EXISTS contestacoes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              devolucao_id INTEGER NOT NULL,
              tipo_divergencia TEXT NOT NULL,
              descricao TEXT NOT NULL,
              valor_contestado REAL NOT NULL,
              evidencia_ids TEXT DEFAULT '[]',
              texto_contestacao TEXT DEFAULT '',
              status TEXT NOT NULL,
              data_abertura TEXT NOT NULL,
              data_resultado TEXT,
              FOREIGN KEY (devolucao_id) REFERENCES devolucoes(id)
            );

            CREATE TABLE IF NOT EXISTS ml_sync_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              tipo TEXT NOT NULL,
              status TEXT NOT NULL,
              iniciado_em TEXT NOT NULL,
              finalizado_em TEXT,
              total_declarado INTEGER DEFAULT 0,
              total_encontrado INTEGER DEFAULT 0,
              total_processado INTEGER DEFAULT 0,
              total_erros INTEGER DEFAULT 0,
              detalhes TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS ml_raw_payloads (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              sync_run_id INTEGER,
              resource_type TEXT NOT NULL,
              resource_id TEXT NOT NULL,
              claim_id TEXT DEFAULT '',
              payload TEXT NOT NULL,
              captured_at TEXT NOT NULL,
              UNIQUE(resource_type, resource_id),
              FOREIGN KEY (sync_run_id) REFERENCES ml_sync_runs(id)
            );

            CREATE TABLE IF NOT EXISTS ml_reconciliation_diffs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              sync_run_id INTEGER NOT NULL,
              tipo TEXT NOT NULL,
              severidade TEXT NOT NULL,
              referencia TEXT DEFAULT '',
              detalhe TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY (sync_run_id) REFERENCES ml_sync_runs(id)
            );

            CREATE TABLE IF NOT EXISTS ml_trace_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              trace_id TEXT NOT NULL,
              sync_run_id INTEGER,
              step TEXT NOT NULL,
              status TEXT NOT NULL,
              duration_ms INTEGER DEFAULT 0,
              claim_id TEXT DEFAULT '',
              details TEXT DEFAULT '{}',
              created_at TEXT NOT NULL,
              FOREIGN KEY (sync_run_id) REFERENCES ml_sync_runs(id)
            );

            CREATE TABLE IF NOT EXISTS ml_claim_classifications (
              claim_id TEXT PRIMARY KEY,
              pedido_id TEXT DEFAULT '',
              order_ids TEXT DEFAULT '[]',
              status TEXT DEFAULT '',
              stage TEXT DEFAULT '',
              claim_type TEXT DEFAULT '',
              reason_id TEXT DEFAULT '',
              return_id TEXT DEFAULT '',
              return_status TEXT DEFAULT '',
              shipment_status TEXT DEFAULT '',
              shipment_destination TEXT DEFAULT '',
              seller_actions TEXT DEFAULT '[]',
              bucket TEXT NOT NULL,
              regra TEXT DEFAULT '',
              last_updated TEXT DEFAULT '',
              payload TEXT DEFAULT '{}',
              active INTEGER DEFAULT 1,
              updated_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(devolucoes)").fetchall()}
        extra_columns = {
            "ml_claim_id": "TEXT",
            "ml_status": "TEXT DEFAULT ''",
            "ml_stage": "TEXT DEFAULT ''",
            "ml_return_status": "TEXT DEFAULT ''",
            "ultima_sincronizacao_ml": "TEXT",
            "ml_destino_devolucao": "TEXT DEFAULT ''",
            "ml_tipo_logistica": "TEXT DEFAULT ''",
            "prazo_resolucao": "TEXT",
            "prioridade_prazo": "TEXT DEFAULT ''",
            "requer_acao": "INTEGER DEFAULT 1",
            "acao_recomendada": "TEXT DEFAULT ''",
            "produto_imagem": "TEXT DEFAULT ''",
            "chegada_status": "TEXT DEFAULT ''",
            "mediacao_mensagem": "TEXT DEFAULT ''",
            "ml_ativo": "INTEGER DEFAULT 1",
            "etapa_checklist_atual": "INTEGER DEFAULT 0",
            "conteudo_progresso_checklist": "TEXT DEFAULT '{}'",
            "ml_valor_pago": "REAL DEFAULT 0",
            "ml_valor_reembolsado": "REAL DEFAULT 0",
            "ml_taxa_venda": "REAL DEFAULT 0",
            "ml_custo_envio": "REAL DEFAULT 0",
            "ml_status_pagamento": "TEXT DEFAULT ''",
            "ml_return_id": "TEXT DEFAULT ''",
            "ml_return_subtype": "TEXT DEFAULT ''",
            "ml_status_money": "TEXT DEFAULT ''",
            "ml_refund_at": "TEXT DEFAULT ''",
            "ml_seller_status": "TEXT DEFAULT ''",
            "ml_seller_reason": "TEXT DEFAULT ''",
            "ml_product_condition": "TEXT DEFAULT ''",
            "ml_return_reviews": "TEXT DEFAULT '[]'",
        }
        for name, definition in extra_columns.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE devolucoes ADD COLUMN {name} {definition}")
        checklist_columns = {row["name"] for row in conn.execute("PRAGMA table_info(checklists)").fetchall()}
        checklist_extra_columns = {
            "embalagem_rasgada": "INTEGER DEFAULT 0",
            "produto_amassado": "INTEGER DEFAULT 0",
            "produto_riscado": "INTEGER DEFAULT 0",
            "produto_quebrado": "INTEGER DEFAULT 0",
            "produto_sujo": "INTEGER DEFAULT 0",
            "faltando_acessorios": "INTEGER DEFAULT 0",
            "produto_errado": "INTEGER DEFAULT 0",
            "sem_embalagem_original": "INTEGER DEFAULT 0",
        }
        for name, definition in checklist_extra_columns.items():
            if name not in checklist_columns:
                conn.execute(f"ALTER TABLE checklists ADD COLUMN {name} {definition}")
        classification_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ml_claim_classifications)").fetchall()}
        classification_extra_columns = {
            "produto_nome": "TEXT DEFAULT ''",
            "produto_imagem": "TEXT DEFAULT ''",
            "valor_pago": "REAL DEFAULT 0",
            "taxa_venda": "REAL DEFAULT 0",
            "ml_tipo_logistica": "TEXT DEFAULT ''",
            "motivo_label": "TEXT DEFAULT ''",
            "pack_id": "TEXT DEFAULT ''",
            "mandatory": "INTEGER DEFAULT 0",
            "due_date": "TEXT DEFAULT ''",
            "date_created": "TEXT DEFAULT ''",
        }
        for name, definition in classification_extra_columns.items():
            if name not in classification_columns:
                conn.execute(f"ALTER TABLE ml_claim_classifications ADD COLUMN {name} {definition}")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_devolucoes_ml_claim_id
            ON devolucoes(ml_claim_id)
            WHERE ml_claim_id IS NOT NULL AND ml_claim_id != ''
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ml_raw_payloads_claim
            ON ml_raw_payloads(claim_id, resource_type)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ml_sync_runs_tipo_status
            ON ml_sync_runs(tipo, status, iniciado_em)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ml_trace_events_trace
            ON ml_trace_events(trace_id, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ml_claim_classifications_bucket
            ON ml_claim_classifications(active, bucket)
            """
        )
        total = conn.execute("SELECT COUNT(*) AS total FROM devolucoes").fetchone()["total"]
        if total == 0:
            conn.executemany(
                """
                INSERT INTO devolucoes (
                  marketplace, pedido_id, cliente_nome, produto_nome, motivo_devolucao,
                  valor_produto, status, data_solicitacao, codigo_rastreio,
                  valor_recuperado, valor_perdido, observacao_final
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "Mercado Livre",
                        "MLB123",
                        "Joao da Silva",
                        "Capa de Banco",
                        "Produto diferente do anunciado",
                        89.9,
                        "aguardando_produto",
                        "2026-04-23 10:30:00",
                        "TRACK123ML",
                        0,
                        0,
                        "",
                    )
                ],
            )


def require_login():
    if not session.get("logged_in"):
        abort(401)


def current_env() -> dict:
    return {**os.environ, **dotenv_values(ENV_PATH)}


def env_int(name: str, default: int) -> int:
    try:
        return int(current_env().get(name, default))
    except (TypeError, ValueError):
        return default


def ml_worker_count(name: str, default: int) -> int:
    return max(1, min(env_int(name, default), 8))


def set_env_values(updates: dict[str, str]) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def public_url_for(endpoint: str, **values) -> str:
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    if "trycloudflare.com" in host:
        scheme = "https"
    return f"{scheme}://{host}{url_for(endpoint, **values)}"


def mercadolivre_redirect_uri(env_values: dict) -> str:
    configured = (env_values.get("ML_REDIRECT_URI") or "").strip()
    return configured or public_url_for("mercadolivre_auth_callback")


def ml_access_token(force_refresh: bool = False) -> str:
    env_values = current_env()
    if env_values.get("ML_ACCESS_TOKEN") and not force_refresh:
        return env_values["ML_ACCESS_TOKEN"]
    refresh_token = env_values.get("ML_REFRESH_TOKEN")
    if not refresh_token:
        raise RuntimeError("Token do Mercado Livre expirou e nao ha ML_REFRESH_TOKEN. Clique em Autorizar ML novamente.")
    response = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": env_values.get("ML_CLIENT_ID", ""),
            "client_secret": env_values.get("ML_CLIENT_SECRET", ""),
            "refresh_token": refresh_token,
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(
            "Nao foi possivel renovar o token do Mercado Livre. "
            "Clique em Autorizar ML novamente para gerar novas credenciais. "
            f"Resposta: {response.text}"
        )
    data = response.json()
    set_env_values(
        {
            "ML_ACCESS_TOKEN": data.get("access_token", ""),
            "ML_REFRESH_TOKEN": data.get("refresh_token", refresh_token),
        }
    )
    return data["access_token"]


def ml_get(path: str, params: dict | None = None) -> dict:
    response = ml_request("GET", path, params=params)
    return response.json() if response.text else {}


def ml_request(method: str, path: str, params: dict | None = None, body: dict | None = None) -> requests.Response:
    def send(token: str) -> requests.Response:
        return requests.request(
            method,
            f"https://api.mercadolibre.com{path}",
            params=params or {},
            json=body,
            headers={"Authorization": f"Bearer {token}", "x-format-new": "true", "Accept": "application/json"},
            timeout=10,
        )

    response = send(ml_access_token())
    if response.status_code == 401:
        response = send(ml_access_token(force_refresh=True))
    if not response.ok:
        raise RuntimeError(f"Mercado Livre respondeu {response.status_code}: {response.text}")
    return response


def extract_pedido_id(raw_value: str) -> str:
    raw = str(raw_value or "").strip()
    if not raw:
        return ""
    numbers = re.findall(r"\d{8,}", raw)
    if numbers:
        return max(numbers, key=len)
    digits = re.sub(r"\D", "", raw)
    return digits


def normalize_lookup_value(value: str | int | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def lookup_matches(query: str, *values: str | int | None) -> bool:
    normalized_query = normalize_lookup_value(query)
    query_digits = re.sub(r"\D", "", str(query or ""))
    if not normalized_query and not query_digits:
        return False
    for value in values:
        normalized_value = normalize_lookup_value(value)
        value_digits = re.sub(r"\D", "", str(value or ""))
        if normalized_query and normalized_query == normalized_value:
            return True
        if query_digits and query_digits == value_digits:
            return True
    return False


def ml_error_response(exc: Exception, pedido_id: str) -> tuple[dict, int]:
    message = str(exc)
    if "404" in message or "order_not_found" in message:
        return (
            {
                "mensagem": "Nao encontrei esse pedido/rastreio no Mercado Livre.",
                "erro": "order_not_found",
                "pedido_id": pedido_id,
                "orientacao": (
                    "Confira se esse numero e o ID do pedido, pacote ou rastreio de devolucao da sua conta Mercado Livre. "
                    "Codigo do anuncio ou pedido de outra conta nao abre aqui."
                ),
            },
            404,
        )
    if "401" in message or "unauthorized" in message.lower():
        return (
            {
                "mensagem": "Mercado Livre recusou o token. Clique em Sincronizar/Autorizar ML novamente.",
                "erro": "unauthorized",
                "pedido_id": pedido_id,
            },
            401,
        )
    return (
        {
            "mensagem": "Nao foi possivel importar o pedido.",
            "erro": message,
            "pedido_id": pedido_id,
        },
        400,
    )


def add_days_iso(value: str | None, days: int) -> str:
    base = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.now(timezone.utc)
    return (base + timedelta(days=days)).isoformat()


def map_ml_status(claim: dict, retorno: dict | None) -> str:
    claim_status = str(claim.get("status") or "").lower()
    return_status = str((retorno or {}).get("status") or "").lower()
    stage = str(claim.get("stage") or "").lower()
    actions = [
        action.get("action")
        for player in claim.get("players") or []
        for action in player.get("available_actions") or []
    ]
    if any(action in {"return_review_ok", "return_review_fail", "return_review_unified_ok", "return_review_unified_fail"} for action in actions):
        return "produto_recebido"
    if claim_status == "closed" or "finished" in return_status:
        return "encerrado"
    if "delivered" in return_status or "received" in return_status:
        return "produto_recebido"
    if "dispute" in stage:
        return "contestacao_aberta"
    return "aguardando_produto"


def claim_available_actions(claim: dict) -> list[dict]:
    return [
        action
        for player in claim.get("players") or []
        for action in player.get("available_actions") or []
    ]


def has_return_review_action(claim: dict) -> bool:
    review_actions = {"return_review_unified_ok", "return_review_unified_fail"}
    return any(action.get("action") in review_actions for action in claim_available_actions(claim))


def has_seller_action(claim: dict, actions: set[str]) -> bool:
    return bool(set(action_names(claim)).intersection(actions))


def action_names(claim: dict) -> list[str]:
    return sorted({str(action.get("action") or "") for action in claim_available_actions(claim) if action.get("action")})


def claim_has_listed_seller_action(claim: dict) -> bool:
    actions = set(action_names(claim))
    review_actions = {"return_review_unified_ok", "return_review_unified_fail"}
    if actions.intersection(review_actions):
        return True
    status = claim.get("status")
    if status == "opened" and "send_message_to_mediator" in actions:
        return True
    if status == "closed" and claim_benefited_complainant_only(claim) and claim_touched_after_resolution(claim):
        return True
    return False


def review_due_date(claim: dict) -> str | None:
    for action in claim_available_actions(claim):
        if action.get("action") in {"return_review_ok", "return_review_unified_ok"} and action.get("due_date"):
            return action.get("due_date")
    return None


def ml_confirm_return_review_ok(claim_id: str | int) -> dict:
    claim = ml_get(f"/post-purchase/v1/claims/{claim_id}")
    actions = {action.get("action") for action in claim_available_actions(claim)}
    review_actions = {"return_review_ok", "return_review_unified_ok"}
    if not actions.intersection(review_actions):
        raise RuntimeError(
            "O Mercado Livre nao disponibilizou a acao de confirmar chegada correta para esta devolucao."
        )

    retorno = ml_get(f"/post-purchase/v2/claims/{claim_id}/returns")
    return_id = retorno.get("id")
    errors: list[str] = []
    if return_id:
        try:
            response = ml_request("POST", f"/post-purchase/v1/returns/{return_id}/return-review", body={})
            return {
                "executed": True,
                "endpoint": "return-review",
                "return_id": return_id,
                "response": response.json() if response.text else {},
            }
        except Exception as exc:
            errors.append(str(exc))

    if "return_review_ok" in actions:
        try:
            response = ml_request("POST", f"/post-purchase/v1/claims/{claim_id}/actions/return-review-ok")
            return {
                "executed": True,
                "endpoint": "return-review-ok",
                "return_id": return_id,
                "response": response.json() if response.text else {},
            }
        except Exception as exc:
            errors.append(str(exc))

    detail = " | ".join(errors) if errors else "sem detalhe retornado"
    raise RuntimeError(f"Nao foi possivel concluir a devolucao no Mercado Livre: {detail}")


def ml_return_shipments(retorno: dict | None) -> list[dict]:
    retorno = retorno or {}
    shipments = retorno.get("shipments")
    if isinstance(shipments, list) and shipments:
        return [shipment or {} for shipment in shipments]
    shipping = retorno.get("shipping")
    if isinstance(shipping, dict) and shipping:
        return [shipping]
    shipment = retorno.get("shipment")
    if isinstance(shipment, dict) and shipment:
        return [shipment]
    return [{}]


def start_ml_sync_run(tipo: str, detalhes: dict | None = None) -> int:
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO ml_sync_runs (tipo, status, iniciado_em, detalhes)
            VALUES (?, 'running', ?, ?)
            """,
            [tipo, now_iso(), json_dumps(detalhes or {})],
        )
        return int(cur.lastrowid)


def finish_ml_sync_run(
    sync_run_id: int,
    *,
    status: str,
    total_declarado: int = 0,
    total_encontrado: int = 0,
    total_processado: int = 0,
    total_erros: int = 0,
    detalhes: dict | None = None,
) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE ml_sync_runs
            SET status = ?, finalizado_em = ?, total_declarado = ?, total_encontrado = ?,
                total_processado = ?, total_erros = ?, detalhes = ?
            WHERE id = ?
            """,
            [
                status,
                now_iso(),
                int(total_declarado or 0),
                int(total_encontrado or 0),
                int(total_processado or 0),
                int(total_erros or 0),
                json_dumps(detalhes or {}),
                sync_run_id,
            ],
        )


def save_ml_raw_payload(sync_run_id: int | None, resource_type: str, resource_id: str | int | None, payload: dict, claim_id: str | int | None = "") -> None:
    resource_id = str(resource_id or "")
    if not resource_id:
        return
    with db() as conn:
        conn.execute(
            """
            INSERT INTO ml_raw_payloads (
              sync_run_id, resource_type, resource_id, claim_id, payload, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(resource_type, resource_id) DO UPDATE SET
              sync_run_id = excluded.sync_run_id,
              claim_id = excluded.claim_id,
              payload = excluded.payload,
              captured_at = excluded.captured_at
            """,
            [sync_run_id, resource_type, resource_id, str(claim_id or ""), json_dumps(payload), now_iso()],
        )


def add_ml_trace_event(
    trace_id: str | None,
    sync_run_id: int | None,
    step: str,
    *,
    status: str = "ok",
    details: dict | None = None,
    claim_id: str | int | None = "",
    started_at: float | None = None,
) -> None:
    if not trace_id:
        return
    duration_ms = int((perf_counter() - started_at) * 1000) if started_at else 0
    with db() as conn:
        conn.execute(
            """
            INSERT INTO ml_trace_events (
              trace_id, sync_run_id, step, status, duration_ms, claim_id, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                trace_id,
                sync_run_id,
                step,
                status,
                duration_ms,
                str(claim_id or ""),
                json_dumps(details or {}),
                now_iso(),
            ],
        )


def ml_return_reviews(return_id: str | int | None, sync_run_id: int | None = None, claim_id: str | int | None = "") -> dict:
    if not return_id:
        return {"reviews": []}
    try:
        reviews = ml_get(f"/post-purchase/v1/returns/{return_id}/reviews")
        save_ml_raw_payload(sync_run_id, "return_reviews", return_id, reviews, claim_id)
        return reviews
    except Exception:
        return {"reviews": []}


def add_ml_reconciliation_diff(sync_run_id: int, tipo: str, severidade: str, referencia: str, detalhe: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO ml_reconciliation_diffs (
              sync_run_id, tipo, severidade, referencia, detalhe, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [sync_run_id, tipo, severidade, referencia, detalhe, now_iso()],
        )


def order_financials(order: dict | None) -> dict:
    order = order or {}
    payments = order.get("payments") or []
    items = order.get("order_items") or []
    valor_pago = sum(float(payment.get("total_paid_amount") or payment.get("transaction_amount") or 0) for payment in payments)
    valor_reembolsado = sum(
        float(payment.get("total_paid_amount") or payment.get("transaction_amount") or 0)
        for payment in payments
        if str(payment.get("status") or "").lower() in {"refunded", "charged_back"}
    )
    taxa_venda = sum(float(item.get("sale_fee") or 0) for item in items)
    custo_envio = sum(float(payment.get("shipping_cost") or 0) for payment in payments)
    status_pagamento = ",".join(sorted({str(payment.get("status") or "") for payment in payments if payment.get("status")}))
    return {
        "ml_valor_pago": valor_pago,
        "ml_valor_reembolsado": valor_reembolsado,
        "ml_taxa_venda": taxa_venda,
        "ml_custo_envio": custo_envio,
        "ml_status_pagamento": status_pagamento,
    }


def build_ml_devolucao(claim: dict, sync_run_id: int | None = None) -> dict:
    claim_id = claim.get("id")
    resource_id = claim.get("resource_id")
    retorno = None
    order = None
    save_ml_raw_payload(sync_run_id, "claim", claim_id, claim, claim_id)
    try:
        retorno = ml_get(f"/post-purchase/v2/claims/{claim_id}/returns")
        save_ml_raw_payload(sync_run_id, "return", retorno.get("id") or claim_id, retorno, claim_id)
    except Exception:
        retorno = None
    return_id = (retorno or {}).get("id")
    reviews_payload = ml_return_reviews(return_id, sync_run_id, claim_id)
    if resource_id:
        try:
            order = ml_get(f"/orders/{resource_id}")
            save_ml_raw_payload(sync_run_id, "order", order.get("id") or resource_id, order, claim_id)
        except Exception:
            order = None

    item = ((order or {}).get("order_items") or [{}])[0].get("item", {})
    buyer = (order or {}).get("buyer", {})
    buyer_name = " ".join([buyer.get("first_name") or "", buyer.get("last_name") or ""]).strip()
    shipment = ml_return_shipments(retorno)[0]
    destination = (shipment or {}).get("destination", {}).get("name", "")
    return_status = str((retorno or {}).get("status") or "").lower()
    date_base = (retorno or {}).get("last_updated") or (retorno or {}).get("date_created") or claim.get("date_created")
    logistic_type = str(((order or {}).get("shipping") or {}).get("logistic_type") or "").lower()
    order_tags = set((order or {}).get("tags") or [])
    full_ml = logistic_type == "fulfillment" or destination == "warehouse" or (bool((order or {}).get("fulfilled")) and "d2c" not in order_tags)
    precisa_revisao = has_return_review_action(claim)
    picture = item.get("secure_thumbnail") or item.get("thumbnail") or ""
    item_id = item.get("id")
    if item_id and not picture:
        try:
            ml_item = ml_get(f"/items/{item_id}")
            save_ml_raw_payload(sync_run_id, "item", item_id, ml_item, claim_id)
            picture = ml_item.get("secure_thumbnail") or ml_item.get("thumbnail") or ""
            pictures = ml_item.get("pictures") or []
            if pictures:
                picture = pictures[0].get("secure_url") or pictures[0].get("url") or picture
        except Exception:
            picture = ""

    proxima_atender = return_status in {"label_generated", "delivered", "received"} or precisa_revisao

    if precisa_revisao or return_status in {"delivered", "received"}:
        prioridade = "hoje"
        prazo = review_due_date(claim) or now_iso()
        requer_acao = 1
        acao = "Produto esta em Para sua revisao no Mercado Livre. Conferir chegada e decidir se chegou como esperado."
    elif return_status == "label_generated":
        prazo = now_iso()
        if claim.get("reason_id") == "PDD9967":
            prioridade = "retirar_correio"
            acao = "Devolucao para retirar no correio."
        else:
            prioridade = "outros_problemas"
            acao = "Devolucao aguardando envio/postagem do comprador. Acompanhar no Mercado Livre."
        requer_acao = 0
    elif full_ml:
        prioridade = "full_ml"
        prazo = None
        requer_acao = 0
        acao = "Venda Full sem acao de revisao para a loja neste momento. Acompanhar no Mercado Livre."
    elif return_status in {"delivered", "label_generated"}:
        prioridade = "hoje"
        prazo = now_iso()
        requer_acao = 1
        acao = "Produto chegou ou esta com etiqueta. Preparar vistoria e decisao."
    elif return_status == "shipped":
        prioridade = "amanha"
        prazo = add_days_iso(date_base, 1)
        requer_acao = 1
        acao = "Produto a caminho. Separar caso para vistoria."
    else:
        prioridade = "semana"
        prazo = add_days_iso(date_base, 2)
        requer_acao = 1
        acao = "Aguardando andamento. Monitorar prazo."

    unit_price = float(((order or {}).get("order_items") or [{}])[0].get("unit_price") or 0)
    quantity = float(((order or {}).get("order_items") or [{}])[0].get("quantity") or 1)
    display_id = (order or {}).get("pack_id") or resource_id or claim_id
    financials = order_financials(order)
    return {
        "marketplace": "Mercado Livre",
        "pedido_id": str(display_id),
        "cliente_nome": buyer_name or buyer.get("nickname") or str(buyer.get("id") or "Cliente Mercado Livre"),
        "produto_nome": item.get("title") or "Produto Mercado Livre",
        "motivo_devolucao": claim.get("reason_id") or claim.get("reason") or (retorno or {}).get("status") or "Devolucao Mercado Livre",
        "valor_produto": float((order or {}).get("total_amount") or unit_price * quantity or 0),
        "status": map_ml_status(claim, retorno),
        "data_solicitacao": claim.get("date_created") or claim.get("date_opened") or now_iso(),
        "codigo_rastreio": (shipment or {}).get("tracking_number") or (shipment or {}).get("id"),
        "valor_recuperado": 0,
        "valor_perdido": 0,
        "observacao_final": f"Importado do Mercado Livre. Claim {claim_id}.",
        "ml_claim_id": str(claim_id),
        "ml_status": claim.get("status") or "",
        "ml_stage": claim.get("stage") or "",
        "ml_return_status": (retorno or {}).get("status") or "",
        "ml_return_id": str(return_id or ""),
        "ml_return_subtype": (retorno or {}).get("subtype") or "",
        "ml_status_money": (retorno or {}).get("status_money") or "",
        "ml_refund_at": (retorno or {}).get("refund_at") or "",
        "ml_seller_status": (retorno or {}).get("seller_status") or "",
        "ml_seller_reason": (retorno or {}).get("seller_reason") or "",
        "ml_product_condition": (retorno or {}).get("product_condition") or "",
        "ml_return_reviews": json_dumps((reviews_payload or {}).get("reviews") or []),
        "ml_destino_devolucao": destination,
        "ml_tipo_logistica": "full_ml" if full_ml else "seller_address",
        "prazo_resolucao": prazo,
        "prioridade_prazo": prioridade,
        "requer_acao": requer_acao,
        "acao_recomendada": acao,
        "produto_imagem": picture,
        "chegada_status": "",
        "mediacao_mensagem": "",
        "ml_ativo": 1 if proxima_atender else 0,
        **financials,
    }


def build_devolucao_from_order(order: dict, pedido_id_override: str | None = None) -> dict:
    order_item = ((order or {}).get("order_items") or [{}])[0]
    item = order_item.get("item") or {}
    buyer = (order or {}).get("buyer") or {}
    shipping = (order or {}).get("shipping") or {}
    logistic_type = str(shipping.get("logistic_type") or "").lower()
    order_tags = set((order or {}).get("tags") or [])
    full_ml = logistic_type == "fulfillment" or (bool(order.get("fulfilled")) and "d2c" not in order_tags)
    buyer_name = " ".join([buyer.get("first_name") or "", buyer.get("last_name") or ""]).strip()
    item_id = item.get("id")
    picture = item.get("secure_thumbnail") or item.get("thumbnail") or ""

    if item_id and not picture:
      try:
          ml_item = ml_get(f"/items/{item_id}")
          picture = ml_item.get("secure_thumbnail") or ml_item.get("thumbnail") or ""
          pictures = ml_item.get("pictures") or []
          if pictures:
              picture = pictures[0].get("secure_url") or pictures[0].get("url") or picture
      except Exception:
          picture = ""

    unit_price = float(order_item.get("unit_price") or 0)
    quantity = float(order_item.get("quantity") or 1)
    status = "aguardando_plataforma" if full_ml else "produto_recebido"
    financials = order_financials(order)
    return {
        "marketplace": "Mercado Livre",
        "pedido_id": str(pedido_id_override or order.get("pack_id") or order.get("id") or ""),
        "cliente_nome": buyer_name or buyer.get("nickname") or str(buyer.get("id") or "Cliente Mercado Livre"),
        "produto_nome": item.get("title") or "Produto Mercado Livre",
        "motivo_devolucao": "Venda localizada manualmente",
        "valor_produto": float(order.get("total_amount") or unit_price * quantity or 0),
        "status": status,
        "data_solicitacao": order.get("date_created") or now_iso(),
        "codigo_rastreio": str(shipping.get("id") or ""),
        "valor_recuperado": 0,
        "valor_perdido": 0,
        "observacao_final": "Importado pelo ID do pedido/leitor.",
        "ml_claim_id": "",
        "ml_status": order.get("status") or "",
        "ml_stage": "",
        "ml_return_status": "",
        "ml_return_id": "",
        "ml_return_subtype": "",
        "ml_status_money": "",
        "ml_refund_at": "",
        "ml_seller_status": "",
        "ml_seller_reason": "",
        "ml_product_condition": "",
        "ml_return_reviews": "[]",
        "ml_destino_devolucao": "full_ml" if full_ml else "seller_address",
        "ml_tipo_logistica": "full_ml" if full_ml else "organica",
        "prazo_resolucao": None if full_ml else now_iso(),
        "prioridade_prazo": "full_ml" if full_ml else "hoje",
        "requer_acao": 0 if full_ml else 1,
        "acao_recomendada": (
            "Venda Full: aguardar revisao do Mercado Livre antes de agir."
            if full_ml
            else "Venda organica chegou na loja. Revisar produto agora."
        ),
        "produto_imagem": picture,
        "chegada_status": "",
        "mediacao_mensagem": "",
        "ml_ativo": 1,
        **financials,
    }


def find_order_by_identifier(identifier: str) -> tuple[dict, str]:
    identifier = extract_pedido_id(identifier)
    try:
        return ml_get(f"/orders/{identifier}"), "order"
    except Exception as exc:
        if "404" not in str(exc) and "order_not_found" not in str(exc):
            raise

    pack = ml_get(f"/packs/{identifier}")
    orders = pack.get("orders") or []
    if not orders:
        raise RuntimeError(f"Mercado Livre respondeu 404: pack {identifier} sem pedidos vinculados")
    order_id = str(orders[0].get("id") or "")
    if not order_id:
        raise RuntimeError(f"Mercado Livre respondeu 404: pack {identifier} sem order_id")
    order = ml_get(f"/orders/{order_id}")
    order["pack_id"] = order.get("pack_id") or pack.get("id") or identifier
    return order, "pack"


def find_claim_by_tracking(identifier: str) -> dict:
    env_values = current_env()
    user_id = env_values.get("ML_USER_ID", "")
    if not user_id:
        raise RuntimeError("ML_USER_ID nao configurado.")
    claims, _ = ml_claims_search(user_id, "opened", max_pages=10)
    for claim in claims:
        try:
            retorno = ml_get(f"/post-purchase/v2/claims/{claim.get('id')}/returns")
        except Exception:
            continue
        shipment_items = ml_return_shipments(retorno)
        for shipment in shipment_items:
            if lookup_matches(
                identifier,
                (retorno or {}).get("id"),
                (retorno or {}).get("claim_id"),
                (retorno or {}).get("resource_id"),
                (shipment or {}).get("tracking_number"),
                (shipment or {}).get("shipment_id"),
                (shipment or {}).get("id"),
            ):
                return claim
    raise RuntimeError("Mercado Livre respondeu 404: rastreio nao encontrado nas devolucoes abertas")


def build_devolucao_from_identifier(identifier: str) -> dict:
    try:
        order, source = find_order_by_identifier(identifier)
    except Exception as exc:
        if "404" not in str(exc) and "order_not_found" not in str(exc):
            raise
        claim = find_claim_by_tracking(identifier)
        return build_ml_devolucao(claim)
    mediations = order.get("mediations") or []
    for mediation in mediations:
        claim_id = mediation.get("id")
        if not claim_id:
            continue
        try:
            claim = ml_get(f"/post-purchase/v1/claims/{claim_id}")
            item = build_ml_devolucao(claim)
            if source == "pack":
                item["pedido_id"] = identifier
            return item
        except Exception:
            continue
    return build_devolucao_from_order(order, pedido_id_override=identifier if source == "pack" else None)


def needs_review_item(item: dict | sqlite3.Row) -> bool:
    status = item["status"] if isinstance(item, sqlite3.Row) else item.get("status")
    requer_acao = item["requer_acao"] if isinstance(item, sqlite3.Row) else item.get("requer_acao", 1)
    return status in {"produto_recebido", "divergencia_encontrada", "em_analise"} and int(requer_acao or 0) == 1


def is_full_item(item: dict | sqlite3.Row) -> bool:
    value = item["ml_tipo_logistica"] if isinstance(item, sqlite3.Row) else item.get("ml_tipo_logistica")
    return value == "full_ml"


def resumo_from_items(items: list[dict | sqlite3.Row], fonte: str) -> dict:
    para_revisao = sum(1 for item in items if needs_review_item(item))
    para_retirar = sum(
        1
        for item in items
        if (item["prioridade_prazo"] if isinstance(item, sqlite3.Row) else item.get("prioridade_prazo")) == "retirar_correio"
    )
    outros = max(len(items) - para_revisao - para_retirar, 0)
    return {
        "para_revisao": para_revisao,
        "para_retirar": para_retirar,
        "outros_problemas": outros,
        "total": len(items),
        "fonte": fonte,
    }


def resumo_from_database() -> dict:
    with db() as conn:
        rows = conn.execute("SELECT * FROM devolucoes WHERE marketplace = 'Mercado Livre' AND COALESCE(ml_ativo, 1) = 1").fetchall()
    return resumo_from_items(rows, "banco_sincronizado")


def resumo_from_classification_cache() -> dict:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT bucket, COUNT(*) AS total
            FROM ml_claim_classifications
            WHERE active = 1
            GROUP BY bucket
            """
        ).fetchall()
    counts = {row["bucket"]: int(row["total"] or 0) for row in rows}
    para_revisao = counts.get("para_revisao", 0)
    para_retirar = counts.get("para_retirar", 0)
    outros = counts.get("outros_problemas", 0)
    return {
        "para_revisao": para_revisao,
        "para_retirar": para_retirar,
        "outros_problemas": outros,
        "total": para_revisao + para_retirar + outros,
        "fonte": "cache_classificacao_ml",
    }


def claim_return_status(claim_id: int | str) -> str:
    for _ in range(2):
        try:
            retorno = ml_get(f"/post-purchase/v2/claims/{claim_id}/returns")
            return str(retorno.get("status") or "").lower()
        except Exception:
            continue
    return ""


def claim_return_info(claim_id: int | str) -> dict:
    for _ in range(2):
        try:
            retorno = ml_get(f"/post-purchase/v2/claims/{claim_id}/returns")
            shipment = ml_return_shipments(retorno)[0]
            orders = retorno.get("orders") if isinstance(retorno, dict) else []
            return {
                "return_id": str((retorno or {}).get("id") or ""),
                "status": str((retorno or {}).get("status") or "").lower(),
                "shipment_status": str((shipment or {}).get("status") or "").lower(),
                "shipment_destination": str(((shipment or {}).get("destination") or {}).get("name") or "").lower(),
                "date_created": (retorno or {}).get("date_created") or "",
                "refund_at": str((retorno or {}).get("refund_at") or "").lower(),
                "seller_status": str((retorno or {}).get("seller_status") or "").lower(),
                "seller_reason": str((retorno or {}).get("seller_reason") or "").lower(),
                "product_condition": str((retorno or {}).get("product_condition") or "").lower(),
                "orders": orders if isinstance(orders, list) else [],
                "related_entities": retorno.get("related_entities") if isinstance(retorno, dict) else [],
            }
        except Exception:
            continue
    return {
        "return_id": "",
        "status": "",
        "shipment_status": "",
        "shipment_destination": "",
        "date_created": "",
        "refund_at": "",
        "seller_status": "",
        "seller_reason": "",
        "product_condition": "",
        "orders": [],
        "related_entities": [],
    }


def classify_ml_next_claim(claim: dict, return_info: dict | None = None, today_local=None) -> str | None:
    today_local = today_local or datetime.now().date()
    return_info = return_info or claim_return_info(claim.get("id"))
    return_status = str(return_info.get("status") or "").lower()
    shipment_status = str(return_info.get("shipment_status") or "").lower()
    return_created_today = str(return_info.get("date_created") or "")[:10] == today_local.isoformat()
    reason = claim.get("reason_id") or "sem_motivo"
    updated_value = str(claim.get("last_updated") or claim.get("date_created") or "")
    updated_today = updated_value[:10] == today_local.isoformat()
    review_action = has_return_review_action(claim)

    if review_action:
        claim["_next_kind"] = "revisao"
        claim["_review_due_date"] = review_due_date(claim) or updated_value
        return "revisao"
    if claim.get("status") != "opened":
        return None
    if str(claim.get("type") or "").lower() == "mediations" or str(claim.get("stage") or "").lower() == "dispute":
        return None
    if return_status == "label_generated" and reason == "PDD9967":
        claim["_next_kind"] = "retirar_correio"
        return "retirar_correio"
    if return_status == "label_generated" or (
        return_status in {"shipped", "in_return", "processing"}
        and shipment_status == "ready_to_ship"
        and updated_today
        and return_created_today
    ):
        claim["_next_kind"] = "outros_problemas"
        return "outros_problemas"
    return None


def orders_total(user_id: str, params: dict) -> int:
    data = ml_get("/orders/search", {"seller": user_id, "limit": 1, **params})
    return int((data.get("paging") or {}).get("total") or 0)


def all_ml_claims(user_id: str, *, claim_type: str = "returns") -> list[dict]:
    """Busca claims de devolucao no Mercado Livre: abertos e fechados."""
    claims: list[dict] = []
    seen_claims: set[str] = set()
    for status_filter in ("opened", "closed"):
        pages = env_int("ML_SYNC_MAX_PAGES_OPENED", 10) if status_filter == "opened" else env_int("ML_SYNC_MAX_PAGES_CLOSED", 12)
        max_offset = max(pages, 1) * 100
        for offset in range(0, max_offset, 100):
            data = ml_get(
                "/post-purchase/v1/claims/search",
                {
                    "user_id": user_id,
                    "status": status_filter,
                    "type": claim_type,
                    "limit": 100,
                    "offset": offset,
                    "sort": "date_desc",
                },
            )
            batch = data.get("data") or data.get("results") or []
            for claim in batch:
                claim_id = str(claim.get("id") or "")
                if claim_id and claim_id not in seen_claims:
                    seen_claims.add(claim_id)
                    claims.append(claim)
            if len(batch) < 100:
                break
    return claims


def ml_claims_search(
    user_id: str,
    status: str,
    *,
    claim_type: str = "returns",
    max_pages: int = 10,
    sync_run_id: int | None = None,
    trace_id: str | None = None,
) -> tuple[list[dict], int]:
    claims: list[dict] = []
    total = 0
    for page in range(max_pages):
        page_started = perf_counter()
        offset = page * 100
        params = {
            "user_id": user_id,
            "status": status,
            "type": claim_type,
            "limit": 100,
            "offset": offset,
            "sort": "date_desc",
        }
        data = ml_get("/post-purchase/v1/claims/search", params)
        total = int((data.get("paging") or {}).get("total") or total or 0)
        batch = data.get("data") or data.get("results") or []
        claims.extend(batch)
        add_ml_trace_event(
            trace_id,
            sync_run_id,
            "claims_search_page",
            details={
                "status_filter": status,
                "type": claim_type,
                "page": page + 1,
                "offset": offset,
                "limit": 100,
                "batch": len(batch),
                "total_declarado": total,
                "acumulado": len(claims),
            },
            started_at=page_started,
        )
        if len(batch) < 100 or len(claims) >= total:
            break
    return claims, total or len(claims)


def parse_ml_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def post_sales_filters_from_ml(user_id: str) -> dict:
    opened, opened_total = ml_claims_search(user_id, "opened", max_pages=5)
    closed_recent, closed_total = ml_claims_search(user_id, "closed", max_pages=3)
    week_start = datetime.now(timezone.utc) - timedelta(days=7)
    today_local = datetime.now().date()
    yesterday_start = (today_local - timedelta(days=1)).isoformat() + "T00:00:00.000-00:00"
    monday = today_local - timedelta(days=today_local.weekday())
    operational_week_start = monday.isoformat() + "T16:00:00.000-00:00"
    return_info_by_claim: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=ml_worker_count("ML_SYNC_MANUAL_WORKERS", 4)) as executor:
        futures = {executor.submit(claim_return_info, claim.get("id")): claim for claim in opened}
        for future in as_completed(futures):
            claim = futures[future]
            return_info_by_claim[str(claim.get("id") or "")] = future.result()

    para_revisao = 0
    para_retirar = 0
    outros_problemas = 0
    a_caminho = 0
    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}

    for claim in opened:
        claim_id = str(claim.get("id") or "")
        return_info = return_info_by_claim.get(claim_id, {})
        return_status = return_info.get("status", "")
        shipment_status = return_info.get("shipment_status", "")
        return_created_today = str(return_info.get("date_created") or "")[:10] == today_local.isoformat()
        reason = claim.get("reason_id") or "sem_motivo"
        status_counts[return_status or "sem_status"] = status_counts.get(return_status or "sem_status", 0) + 1
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

        kind = classify_ml_next_claim(claim, return_info, today_local)
        if kind == "revisao":
            para_revisao += 1
        elif kind == "retirar_correio":
            para_retirar += 1
        elif kind == "outros_problemas":
            outros_problemas += 1

        if return_status in {"label_generated", "shipped", "in_return", "processing"}:
            a_caminho += 1

    proximas_total = para_revisao + para_retirar + outros_problemas
    concluidas_semana = 0
    for claim in closed_recent:
        updated = parse_ml_datetime(claim.get("last_updated") or claim.get("date_closed") or claim.get("date_created"))
        if updated and updated >= week_start:
            concluidas_semana += 1
    try:
        a_caminho_orders = orders_total(user_id, {"shipping.status": "shipped", "order.date_created.from": yesterday_start})
        concluidas_orders = orders_total(user_id, {"shipping.status": "delivered", "order.date_created.from": yesterday_start})
        nao_concluidas_orders = sum(
            orders_total(user_id, {"shipping.status": status, "order.date_created.from": operational_week_start})
            for status in ("ready_to_ship", "shipped", "pending")
        )
    except Exception:
        a_caminho_orders = a_caminho
        concluidas_orders = concluidas_semana
        nao_concluidas_orders = opened_total

    return {
        "fonte": "mercado_livre_ao_vivo",
        "proximas": {
            "total": proximas_total,
            "para_revisao": para_revisao,
            "para_retirar": para_retirar,
            "outros_problemas": outros_problemas,
        },
        "andamento": {
            "a_caminho": a_caminho_orders,
            "concluidas_ultima_semana": concluidas_orders,
            "nao_concluidas": nao_concluidas_orders,
            "total_fechadas_ml": closed_total,
        },
        "status_ml": dict(sorted(status_counts.items())),
        "motivos_ml": dict(sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)),
        "amostra_abertos": [
            {
                "claim_id": claim.get("id"),
                "pedido_id": claim.get("resource_id"),
                "motivo": claim.get("reason_id"),
                "status_devolucao": (return_info_by_claim.get(str(claim.get("id") or ""), {}) or {}).get("status", ""),
                "status_claim": claim.get("status"),
                "stage": claim.get("stage"),
                "atualizado_em": claim.get("last_updated"),
            }
            for claim in opened[:20]
        ],
    }


def ml_live_claims_for_queue(user_id: str, *, max_pages: int = 3) -> tuple[list[dict], dict]:
    claims: list[dict] = []
    seen: set[str] = set()
    declared: dict[str, int] = {}
    searches = (
        ("returns", "opened", max_pages),
        ("returns", "closed", env_int("ML_LIVE_QUEUE_CLOSED_PAGES", 1)),
        ("mediations", "opened", max_pages),
        ("mediations", "closed", env_int("ML_LIVE_QUEUE_CLOSED_PAGES", 1)),
    )
    for claim_type, status_filter, pages in searches:
        batch, total = ml_claims_search(user_id, status_filter, claim_type=claim_type, max_pages=pages)
        declared[f"{claim_type}_{status_filter}"] = total
        for claim in batch:
            if not claim_has_listed_seller_action(claim):
                continue
            claim_id = str(claim.get("id") or "")
            if claim_id and claim_id not in seen:
                seen.add(claim_id)
                claims.append(claim)
    return claims, declared


def opened_claims_for_review_and_returns(user_id: str, sync_run_id: int | None = None, trace_id: str | None = None) -> tuple[list[dict], int]:
    claims: list[dict] = []
    seen: set[str] = set()
    total = 0
    searches = (
        ("returns", "opened", env_int("ML_SYNC_MAX_PAGES_OPENED", 10)),
        ("returns", "closed", env_int("ML_SYNC_MAX_PAGES_CLOSED_REVIEW", 2)),
        ("mediations", "opened", env_int("ML_SYNC_MAX_PAGES_MEDIATIONS", 3)),
        ("mediations", "closed", env_int("ML_SYNC_MAX_PAGES_CLOSED_REVIEW", 2)),
    )
    for claim_type, status_filter, max_pages in searches:
        batch, declared = ml_claims_search(
            user_id,
            status_filter,
            claim_type=claim_type,
            max_pages=max_pages,
            sync_run_id=sync_run_id,
            trace_id=trace_id,
        )
        total += int(declared or 0)
        for claim in batch:
            claim_id = str(claim.get("id") or "")
            if claim_id and claim_id not in seen:
                seen.add(claim_id)
                claims.append(claim)
    return claims, total or len(claims)


def classify_ml_live_queue_claim(claim: dict, return_info: dict) -> tuple[str, str]:
    actions = set(action_names(claim))
    return_status = str(return_info.get("status") or "").lower()
    shipment_status = str(return_info.get("shipment_status") or "").lower()
    destination = str(return_info.get("shipment_destination") or "").lower()
    reason = str(claim.get("reason_id") or "")

    review_actions = {"return_review_unified_ok", "return_review_unified_fail"}
    has_review = bool(actions.intersection(review_actions))
    return_related = return_info.get("related_entities") or []
    already_reviewed = "reviews" in return_related
    if has_review and not already_reviewed:
        return "para_revisao", "seller_available_action:return_review"
    if "send_message_to_mediator" in actions:
        if return_status == "delivered":
            return "outros_problemas", "seller_available_action:send_message_to_mediator"
        return "fora_da_fila", f"mediator_return_not_delivered:{return_status}"
    if (
        str(claim.get("status") or "") == "closed"
        and claim_benefited_complainant_only(claim)
        and claim_touched_after_resolution(claim)
        and return_status == "delivered"
        and not already_reviewed
    ):
        return "outros_problemas", "closed_touched_with_return_delivered"
    if str(claim.get("type") or "") == "mediations" or str(claim.get("stage") or "") == "dispute":
        return "fora_da_fila", "mediation_without_return_review_action"
    if return_status == "label_generated" and reason == "PDD9967":
        return "para_retirar", "return_label_generated_with_pickup_reason"
    if str(claim.get("status") or "") == "opened" and return_status in {"label_generated", "shipped", "in_return", "processing"}:
        return "outros_problemas", f"return_in_progress:{return_status}:{shipment_status}:{destination}"
    return "fora_da_fila", f"no_matching_queue_rule:{return_status}:{shipment_status}"


def cached_claim_classification(claim_id: str | int, last_updated: str | None) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM ml_claim_classifications WHERE claim_id = ? LIMIT 1",
            [str(claim_id or "")],
        ).fetchone()
    payload = json.loads(row["payload"] or "{}") if row else {}
    if (
        not row
        or row["last_updated"] != (last_updated or "")
        or payload.get("classifier_version") != ML_CLASSIFIER_VERSION
        or payload.get("enrichment_version") != ML_ENRICHMENT_VERSION
    ):
        return None
    data = dict(row)
    data["seller_actions"] = json.loads(data.get("seller_actions") or "[]")
    data["order_ids"] = json.loads(data.get("order_ids") or "[]")
    data["payload"] = payload
    data["cache_hit"] = True
    return data


def save_claim_classification(item: dict) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO ml_claim_classifications (
              claim_id, pedido_id, order_ids, status, stage, claim_type, reason_id,
              return_id, return_status, shipment_status, shipment_destination,
              seller_actions, bucket, regra, last_updated, payload, active, updated_at,
              produto_nome, produto_imagem, valor_pago, taxa_venda, ml_tipo_logistica,
              motivo_label, pack_id, mandatory, due_date, date_created
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id) DO UPDATE SET
              pedido_id = excluded.pedido_id,
              order_ids = excluded.order_ids,
              status = excluded.status,
              stage = excluded.stage,
              claim_type = excluded.claim_type,
              reason_id = excluded.reason_id,
              return_id = excluded.return_id,
              return_status = excluded.return_status,
              shipment_status = excluded.shipment_status,
              shipment_destination = excluded.shipment_destination,
              seller_actions = excluded.seller_actions,
              bucket = excluded.bucket,
              regra = excluded.regra,
              last_updated = excluded.last_updated,
              payload = excluded.payload,
              active = 1,
              updated_at = excluded.updated_at,
              produto_nome = excluded.produto_nome,
              produto_imagem = excluded.produto_imagem,
              valor_pago = excluded.valor_pago,
              taxa_venda = excluded.taxa_venda,
              ml_tipo_logistica = excluded.ml_tipo_logistica,
              motivo_label = excluded.motivo_label,
              pack_id = excluded.pack_id,
              mandatory = excluded.mandatory,
              due_date = excluded.due_date,
              date_created = excluded.date_created
            """
            ,
            [
                str(item.get("claim_id") or ""),
                str(item.get("pedido_id") or ""),
                json_dumps(item.get("order_ids") or []),
                str(item.get("status") or ""),
                str(item.get("stage") or ""),
                str(item.get("type") or ""),
                str(item.get("reason_id") or ""),
                str(item.get("return_id") or ""),
                str(item.get("return_status") or ""),
                str(item.get("shipment_status") or ""),
                str(item.get("shipment_destination") or ""),
                json_dumps(item.get("seller_actions") or []),
                str(item.get("bucket") or "fora_da_fila"),
                str(item.get("regra") or ""),
                str(item.get("last_updated") or ""),
                json_dumps(item),
                now_iso(),
                str(item.get("produto_nome") or ""),
                str(item.get("produto_imagem") or ""),
                float(item.get("valor_pago") or 0),
                float(item.get("taxa_venda") or 0),
                str(item.get("ml_tipo_logistica") or ""),
                str(item.get("motivo_label") or ""),
                str(item.get("pack_id") or ""),
                int(item.get("mandatory") or 0),
                str(item.get("due_date") or ""),
                str(item.get("date_created") or ""),
            ],
        )


def bucket_action_meta(detail: dict, bucket: str) -> dict:
    actions = claim_available_actions(detail)
    review_actions = {"return_review_unified_ok", "return_review_unified_fail", "return_review_ok", "return_review_fail"}
    target = None
    if bucket == "para_revisao":
        target = next((a for a in actions if a.get("action") in review_actions), None)
    elif bucket == "outros_problemas":
        target = next((a for a in actions if a.get("action") == "send_message_to_mediator"), None)
    if not target:
        target = next((a for a in actions if a.get("due_date")), None)
    return {
        "mandatory": int(bool(target and target.get("mandatory"))),
        "due_date": str((target or {}).get("due_date") or ""),
    }


def order_visuals(order: dict | None, claim_id: str | int) -> dict:
    order = order or {}
    order_item = ((order.get("order_items") or []) + [{}])[0]
    item = order_item.get("item") or {}
    shipping = order.get("shipping") or {}
    logistic_type = str(shipping.get("logistic_type") or "").lower()
    order_tags = set(order.get("tags") or [])
    full_ml = logistic_type == "fulfillment" or (bool(order.get("fulfilled")) and "d2c" not in order_tags)
    picture = item.get("secure_thumbnail") or item.get("thumbnail") or ""
    item_id = item.get("id")
    if item_id and not picture:
        try:
            ml_item = ml_get(f"/items/{item_id}")
            picture = ml_item.get("secure_thumbnail") or ml_item.get("thumbnail") or ""
            pictures = ml_item.get("pictures") or []
            if pictures:
                picture = pictures[0].get("secure_url") or pictures[0].get("url") or picture
        except Exception:
            picture = ""
    financials = order_financials(order)
    return {
        "produto_nome": item.get("title") or "",
        "produto_imagem": picture or "",
        "valor_pago": float(financials.get("ml_valor_pago") or 0),
        "taxa_venda": float(financials.get("ml_taxa_venda") or 0),
        "ml_tipo_logistica": "full_ml" if full_ml else "seller_address",
        "pack_id": str(order.get("pack_id") or ""),
    }


def fetch_order_for_claim(claim_detail: dict, return_info: dict) -> dict | None:
    resource_id = claim_detail.get("resource_id")
    candidates: list[str] = []
    if resource_id:
        candidates.append(str(resource_id))
    for order in return_info.get("orders") or []:
        order_id = order.get("order_id")
        if order_id:
            candidates.append(str(order_id))
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return ml_get(f"/orders/{candidate}")
        except Exception:
            continue
    return None


def inspect_claim_for_queue(claim: dict, *, use_cache: bool = True) -> tuple[dict, bool]:
    claim_id = str(claim.get("id") or "")
    last_updated = str(claim.get("last_updated") or "")
    if use_cache:
        cached = cached_claim_classification(claim_id, last_updated)
        if cached:
            payload = dict(cached.get("payload") or {})
            payload["cache_hit"] = True
            return payload, True
    detail = ml_get(f"/post-purchase/v1/claims/{claim_id}")
    return_info = claim_return_info(claim_id)
    bucket, rule = classify_ml_live_queue_claim(detail, return_info)
    orders = return_info.get("orders") or []
    order_ids = [str(order.get("order_id")) for order in orders if order.get("order_id")]
    order_payload = fetch_order_for_claim(detail, return_info)
    visuals = order_visuals(order_payload, claim_id)
    action_meta = bucket_action_meta(detail, bucket)
    reason_id = detail.get("reason_id")
    item = {
        "claim_id": claim_id,
        "pedido_id": str(detail.get("resource_id") or ""),
        "order_ids": order_ids,
        "status": detail.get("status"),
        "stage": detail.get("stage"),
        "type": detail.get("type"),
        "reason_id": reason_id,
        "return_id": return_info.get("return_id"),
        "return_status": return_info.get("status"),
        "shipment_status": return_info.get("shipment_status"),
        "shipment_destination": return_info.get("shipment_destination"),
        "seller_actions": action_names(detail),
        "bucket": bucket,
        "regra": rule,
        "date_created": detail.get("date_created"),
        "last_updated": detail.get("last_updated") or last_updated,
        "cache_hit": False,
        "classifier_version": ML_CLASSIFIER_VERSION,
        "enrichment_version": ML_ENRICHMENT_VERSION,
        "produto_nome": visuals["produto_nome"],
        "produto_imagem": visuals["produto_imagem"],
        "valor_pago": visuals["valor_pago"],
        "taxa_venda": visuals["taxa_venda"],
        "ml_tipo_logistica": visuals["ml_tipo_logistica"],
        "pack_id": visuals["pack_id"],
        "motivo_label": motivo_label(reason_id),
        "mandatory": action_meta["mandatory"],
        "due_date": action_meta["due_date"],
    }
    save_claim_classification(item)
    return item, False


def apply_ml_queue_window(rows: list[dict]) -> None:
    for item in rows:
        regra = item.get("regra", "")
        if item["bucket"] == "fora_da_fila" and ":outside_recent_window" in regra:
            item["bucket"] = "outros_problemas"
            item["regra"] = regra.replace(":outside_recent_window", "")
    outros_limit = env_int("ML_LIVE_QUEUE_OUTROS_LIMIT", 21)
    if outros_limit <= 0:
        return
    outros = [item for item in rows if item["bucket"] == "outros_problemas"]
    outros_ativos = {
        item["claim_id"]
        for item in sorted(outros, key=lambda item: item.get("last_updated") or "", reverse=True)[:outros_limit]
    }
    for item in rows:
        if item["bucket"] == "outros_problemas" and item["claim_id"] not in outros_ativos:
            item["bucket"] = "fora_da_fila"
            item["regra"] = f"{item.get('regra', '')}:outside_recent_window"


def ml_live_return_queue(user_id: str) -> dict:
    started = perf_counter()
    claims, declared = ml_live_claims_for_queue(user_id, max_pages=env_int("ML_LIVE_QUEUE_MAX_PAGES", 3))
    rows: list[dict] = []
    cache_hits = 0
    cache_misses = 0

    def inspect_claim(claim: dict) -> dict:
        item, _ = inspect_claim_for_queue(claim)
        return item

    with ThreadPoolExecutor(max_workers=ml_worker_count("ML_LIVE_QUEUE_WORKERS", 4)) as executor:
        futures = {executor.submit(inspect_claim, claim): claim for claim in claims}
        for future in as_completed(futures):
            try:
                item = future.result()
                rows.append(item)
                if item.get("cache_hit"):
                    cache_hits += 1
                else:
                    cache_misses += 1
            except Exception as exc:
                claim = futures[future]
                rows.append(
                    {
                        "claim_id": str(claim.get("id") or ""),
                        "pedido_id": str(claim.get("resource_id") or ""),
                        "status": claim.get("status"),
                        "stage": claim.get("stage"),
                        "type": claim.get("type"),
                        "reason_id": claim.get("reason_id"),
                        "bucket": "erro",
                        "regra": str(exc),
                    }
                )

    bucket_order = {"para_revisao": 1, "para_retirar": 2, "outros_problemas": 3, "fora_da_fila": 4, "erro": 5}
    rows.sort(key=lambda item: (bucket_order.get(item["bucket"], 9), item.get("last_updated") or ""), reverse=False)
    apply_ml_queue_window(rows)
    proximas = {
        "para_revisao": sum(1 for item in rows if item["bucket"] == "para_revisao"),
        "para_retirar": sum(1 for item in rows if item["bucket"] == "para_retirar"),
        "outros_problemas": sum(1 for item in rows if item["bucket"] == "outros_problemas"),
    }
    proximas["total"] = proximas["para_revisao"] + proximas["para_retirar"] + proximas["outros_problemas"]
    return {
        "fonte": "mercado_livre_live_queue_v2",
        "duracao_ms": int((perf_counter() - started) * 1000),
        "declarados": declared,
        "inspecionados": len(rows),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "proximas": proximas,
        "fora_da_fila": sum(1 for item in rows if item["bucket"] == "fora_da_fila"),
        "erros": sum(1 for item in rows if item["bucket"] == "erro"),
        "itens": rows,
    }


def opened_claims_for_next_attendance(user_id: str, sync_run_id: int | None = None, trace_id: str | None = None) -> tuple[list[dict], int]:
    search_started = perf_counter()
    claims, total = opened_claims_for_review_and_returns(user_id, sync_run_id, trace_id)
    add_ml_trace_event(
        trace_id,
        sync_run_id,
        "claims_search_total",
        details={
            "total_declarado": total,
            "total_recebido": len(claims),
            "max_pages_returns": env_int("ML_SYNC_MAX_PAGES_OPENED", 10),
            "max_pages_mediations": env_int("ML_SYNC_MAX_PAGES_MEDIATIONS", 3),
        },
        started_at=search_started,
    )
    for claim in claims:
        save_ml_raw_payload(sync_run_id, "claim", claim.get("id"), claim, claim.get("id"))

    def classify(claim: dict) -> tuple[int, str, dict] | None:
        started = perf_counter()
        detailed_claim = ml_get(f"/post-purchase/v1/claims/{claim.get('id')}")
        return_info = claim_return_info(claim.get("id"))
        kind = classify_ml_next_claim(detailed_claim, return_info)
        add_ml_trace_event(
            trace_id,
            sync_run_id,
            "claim_classified",
            status="ok" if kind else "ignored",
            claim_id=claim.get("id"),
            details={
                "pedido_id": detailed_claim.get("resource_id"),
                "reason_id": detailed_claim.get("reason_id"),
                "claim_status": detailed_claim.get("status"),
                "stage": detailed_claim.get("stage"),
                "type": detailed_claim.get("type"),
                "return_status": return_info.get("status"),
                "shipment_status": return_info.get("shipment_status"),
                "shipment_destination": return_info.get("shipment_destination"),
                "seller_status": return_info.get("seller_status"),
                "seller_reason": return_info.get("seller_reason"),
                "product_condition": return_info.get("product_condition"),
                "seller_actions": action_names(detailed_claim),
                "bucket": kind or "",
            },
            started_at=started,
        )
        if not kind:
            return None
        priority = {"revisao": 1, "retirar_correio": 2, "outros_problemas": 3}[kind]
        return (priority, kind, detailed_claim)

    classified: list[tuple[int, str, dict]] = []
    classify_started = perf_counter()
    with ThreadPoolExecutor(max_workers=ml_worker_count("ML_SYNC_MAX_WORKERS", 4)) as executor:
        futures = [executor.submit(classify, claim) for claim in claims]
        for future in as_completed(futures):
            item = future.result()
            if item:
                classified.append(item)
    add_ml_trace_event(
        trace_id,
        sync_run_id,
        "classification_total",
        details={
            "claims_recebidas": len(claims),
            "claims_classificadas": len(classified),
            "claims_ignoradas": max(len(claims) - len(classified), 0),
        },
        started_at=classify_started,
    )

    # Separar por categoria SEM LIMITES
    revisao_items = [item for item in classified if item[1] == "revisao"]
    retirar_items = [item for item in classified if item[1] == "retirar_correio"]
    outros_items = [item for item in classified if item[1] == "outros_problemas"]

    # Ordenar por relevância
    revisao_items.sort(key=lambda item: item[2].get("_review_due_date") or item[2].get("date_created") or "", reverse=False)
    retirar_items.sort(key=lambda item: item[2].get("date_created") or "", reverse=True)
    outros_items.sort(key=lambda item: item[2].get("date_created") or "", reverse=True)

    # Retornar TODOS SEM LIMITES
    revisao = [claim for _, _, claim in revisao_items]
    retirar = [claim for _, _, claim in retirar_items]
    outros = [claim for _, _, claim in outros_items]

    add_ml_trace_event(
        trace_id,
        sync_run_id,
        "classification_buckets",
        details={
            "para_revisao": len(revisao),
            "para_retirar": len(retirar),
            "outros_problemas": len(outros),
            "total_final": len(revisao) + len(retirar) + len(outros),
        },
    )

    return revisao + retirar + outros, total


def refresh_ml_classification_cache(user_id: str, sync_run_id: int | None = None, trace_id: str | None = None) -> dict:
    started = perf_counter()
    claims, declared = ml_live_claims_for_queue(user_id, max_pages=env_int("ML_LIVE_QUEUE_MAX_PAGES", 3))
    active_ids: set[str] = set()
    rows: list[dict] = []
    cache_hits = 0
    cache_misses = 0
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=ml_worker_count("ML_LIVE_QUEUE_WORKERS", 4)) as executor:
        futures = {executor.submit(inspect_claim_for_queue, claim): claim for claim in claims}
        for future in as_completed(futures):
            claim = futures[future]
            try:
                item, hit = future.result()
                rows.append(item)
                cache_hits += 1 if hit else 0
                cache_misses += 0 if hit else 1
            except Exception as exc:
                errors.append(f"{claim.get('id')}: {exc}")

    apply_ml_queue_window(rows)
    for item in rows:
        save_claim_classification(item)
        if item.get("claim_id"):
            active_ids.add(str(item["claim_id"]))

    with db() as conn:
        conn.execute("UPDATE ml_claim_classifications SET active = 0")
        if active_ids:
            placeholders = ",".join(["?"] * len(active_ids))
            conn.execute(
                f"UPDATE ml_claim_classifications SET active = 1 WHERE claim_id IN ({placeholders})",
                sorted(active_ids),
            )

    resumo = resumo_from_classification_cache()
    result = {
        "duracao_ms": int((perf_counter() - started) * 1000),
        "declarados": declared,
        "inspecionados": len(rows),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "erros": errors[:10],
        "resumo": resumo,
    }
    add_ml_trace_event(trace_id, sync_run_id, "classification_cache_refresh", status="ok" if not errors else "partial", details=result, started_at=started)
    return result


def upsert_ml_devolucao(item: dict) -> str:
    fields = [
        "marketplace",
        "pedido_id",
        "cliente_nome",
        "produto_nome",
        "motivo_devolucao",
        "valor_produto",
        "status",
        "data_solicitacao",
        "codigo_rastreio",
        "valor_recuperado",
        "valor_perdido",
        "observacao_final",
        "ml_claim_id",
        "ml_status",
        "ml_stage",
        "ml_return_status",
        "ultima_sincronizacao_ml",
        "ml_destino_devolucao",
        "ml_tipo_logistica",
        "prazo_resolucao",
        "prioridade_prazo",
        "requer_acao",
        "acao_recomendada",
        "produto_imagem",
        "chegada_status",
        "mediacao_mensagem",
        "ml_ativo",
        "ml_valor_pago",
        "ml_valor_reembolsado",
        "ml_taxa_venda",
        "ml_custo_envio",
        "ml_status_pagamento",
        "ml_return_id",
        "ml_return_subtype",
        "ml_status_money",
        "ml_refund_at",
        "ml_seller_status",
        "ml_seller_reason",
        "ml_product_condition",
        "ml_return_reviews",
    ]
    item = {**item, "ultima_sincronizacao_ml": now_iso(), "ml_ativo": int(item.get("ml_ativo", 1))}
    with db() as conn:
        if item.get("ml_claim_id"):
            row = conn.execute(
                "SELECT * FROM devolucoes WHERE ml_claim_id = ? LIMIT 1",
                [item["ml_claim_id"]],
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM devolucoes WHERE marketplace = 'Mercado Livre' AND pedido_id = ? LIMIT 1",
                [item["pedido_id"]],
            ).fetchone()
        if row:
            same_claim = str(row["ml_claim_id"] or "") == str(item.get("ml_claim_id") or "")
            local_expected_closed = row["status"] == "sem_divergencia" or row["chegada_status"] == "esperado"
            remote_still_requires_action = int(item.get("requer_acao") or 0) == 1
            should_keep_local_final = row["status"] in {"aprovado", "parcial", "reprovado"} or (
                local_expected_closed and not remote_still_requires_action
            )
            if same_claim and should_keep_local_final:
                item["status"] = row["status"]
                item["chegada_status"] = row["chegada_status"] or item.get("chegada_status", "")
                item["requer_acao"] = row["requer_acao"]
                item["ml_ativo"] = 0
            assignments = ", ".join([f"{field} = ?" for field in fields if field != "marketplace"])
            conn.execute(
                f"UPDATE devolucoes SET {assignments} WHERE id = ?",
                [item.get(field) for field in fields if field != "marketplace"] + [row["id"]],
            )
            return "updated"
        placeholders = ", ".join(["?"] * len(fields))
        conn.execute(
            f"INSERT INTO devolucoes ({', '.join(fields)}) VALUES ({placeholders})",
            [item.get(field) for field in fields],
        )
        return "created"


def existing_ml_devolucao(item: dict) -> sqlite3.Row | None:
    with db() as conn:
        if item.get("ml_claim_id"):
            return conn.execute(
                "SELECT * FROM devolucoes WHERE ml_claim_id = ? LIMIT 1",
                [item["ml_claim_id"]],
            ).fetchone()
        return conn.execute(
            "SELECT * FROM devolucoes WHERE marketplace = 'Mercado Livre' AND pedido_id = ? LIMIT 1",
            [item["pedido_id"]],
        ).fetchone()


@app.get("/")
def home():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return redirect(url_for("devolucoes"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("pin", "").strip() == PIN_MERCADO_LIVRE:
            session["logged_in"] = True
            return redirect(url_for("devolucoes"))
        error = "PIN incorreto."
    return render_template("login.html", error=error, store_url=STORE_URL)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/devolucoes")
def devolucoes():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    env_values = current_env()
    return render_template(
        "devolucoes.html",
        store_url=STORE_URL,
        ml_connected=bool(env_values.get("ML_ACCESS_TOKEN") or env_values.get("ML_REFRESH_TOKEN")),
        status_options=sorted(STATUS_PERMITIDOS),
    )


@app.get("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(UPLOAD_DIR, filename)


@app.get("/mercadolivre/auth/start")
def mercadolivre_auth_start():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    env_values = current_env()
    client_id = env_values.get("ML_CLIENT_ID", "")
    if not client_id:
        abort(400, "ML_CLIENT_ID nao configurado.")
    redirect_uri = mercadolivre_redirect_uri(env_values)
    params = urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri})
    return redirect(f"https://auth.mercadolivre.com.br/authorization?{params}")


@app.get("/mercadolivre/auth/callback")
def mercadolivre_auth_callback():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    code = request.args.get("code", "")
    if not code:
        abort(400, request.args.get("error_description") or "Codigo de autorizacao nao recebido.")
    env_values = current_env()
    redirect_uri = mercadolivre_redirect_uri(env_values)
    response = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": env_values.get("ML_CLIENT_ID", ""),
            "client_secret": env_values.get("ML_CLIENT_SECRET", ""),
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=20,
    )
    if not response.ok:
        abort(400, f"Mercado Livre nao gerou token: {response.text}")
    data = response.json()
    set_env_values(
        {
            "ML_ACCESS_TOKEN": data.get("access_token", ""),
            "ML_REFRESH_TOKEN": data.get("refresh_token", ""),
            "ML_REDIRECT_URI": redirect_uri,
        }
    )
    return redirect(url_for("devolucoes"))


@app.get("/api/devolucoes")
def api_listar_devolucoes():
    require_login()
    clauses = ["1=1"]
    params: list = []
    if request.args.get("incluir_inativos") != "true":
        clauses.append("COALESCE(ml_ativo, 1) = 1")
    for key in ("status", "marketplace"):
        value = request.args.get(key, "").strip()
        if value:
            clauses.append(f"{key} = ?")
            params.append(value)
    busca = request.args.get("busca", "").strip()
    if busca:
        clauses.append(
            "(pedido_id LIKE ? OR cliente_nome LIKE ? OR produto_nome LIKE ? OR codigo_rastreio LIKE ? OR ml_claim_id LIKE ?)"
        )
        params.extend([f"%{busca}%"] * 5)
    prioridade = request.args.get("prioridade", "").strip()
    if prioridade:
        clauses.append("prioridade_prazo = ?")
        params.append(prioridade)
    if request.args.get("requer_acao") == "true":
        clauses.append("requer_acao = 1")
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM devolucoes
            WHERE {' AND '.join(clauses)}
            ORDER BY
              CASE prioridade_prazo
                WHEN 'hoje' THEN 1
                WHEN 'amanha' THEN 2
                WHEN 'semana' THEN 3
                WHEN 'full_ml' THEN 4
                ELSE 5
              END,
              datetime(COALESCE(prazo_resolucao, data_solicitacao)) ASC,
              id DESC
            """,
            params,
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/devolucoes")
def api_criar_devolucao():
    require_login()
    data = request.get_json(force=True)
    required = ["marketplace", "pedido_id", "cliente_nome", "produto_nome", "motivo_devolucao", "valor_produto", "status"]
    if any(not data.get(field) for field in required):
        return jsonify({"mensagem": "Campos obrigatorios nao preenchidos"}), 400
    if data["status"] not in STATUS_PERMITIDOS:
        return jsonify({"mensagem": "Status invalido"}), 400
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO devolucoes (
              marketplace, pedido_id, cliente_nome, produto_nome, motivo_devolucao,
              valor_produto, status, data_solicitacao, codigo_rastreio,
              valor_recuperado, valor_perdido, observacao_final
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, '')
            """,
            [
                data["marketplace"],
                data["pedido_id"],
                data["cliente_nome"],
                data["produto_nome"],
                data["motivo_devolucao"],
                float(data["valor_produto"]),
                data["status"],
                data.get("data_solicitacao") or now_iso(),
                data.get("codigo_rastreio"),
            ],
        )
        item = conn.execute("SELECT * FROM devolucoes WHERE id = ?", [cur.lastrowid]).fetchone()
    return jsonify(dict(item)), 201


@app.post("/api/pedidos/importar")
def api_importar_pedido():
    require_login()
    data = request.get_json(force=True)
    raw_identifier = str(data.get("pedido_id") or "").strip()
    pedido_id = extract_pedido_id(raw_identifier)
    if not raw_identifier:
        return jsonify({"mensagem": "Informe ou leia o ID do pedido/rastreio.", "orientacao": "Use o ID do pedido, pacote ou rastreio da devolucao Mercado Livre."}), 400
    try:
        item = build_devolucao_from_identifier(raw_identifier)
        existing = existing_ml_devolucao(item)
        if existing:
            item["ml_ativo"] = int(existing["ml_ativo"] if existing["ml_ativo"] is not None else 1)
            item["chegada_status"] = existing["chegada_status"] or item.get("chegada_status", "")
            item["mediacao_mensagem"] = existing["mediacao_mensagem"] or item.get("mediacao_mensagem", "")
            if existing["status"] in {"sem_divergencia", "aprovado", "parcial", "reprovado", "encerrado"}:
                item["status"] = existing["status"]
                item["requer_acao"] = existing["requer_acao"]
        action = upsert_ml_devolucao(item)
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM devolucoes WHERE marketplace = 'Mercado Livre' AND pedido_id = ? LIMIT 1",
                [item["pedido_id"]],
            ).fetchone()
        return jsonify({"mensagem": "Pedido importado", "action": action, "devolucao": dict(row)})
    except Exception as exc:
        payload, status_code = ml_error_response(exc, pedido_id or raw_identifier)
        return jsonify(payload), status_code


@app.get("/api/resumo-ml")
def api_resumo_ml():
    require_login()
    return jsonify(resumo_from_classification_cache())


@app.get("/api/devolucoes/filtros-ml")
def api_filtros_ml():
    require_login()
    resumo = resumo_from_classification_cache()
    return jsonify({"fonte": resumo["fonte"], "proximas": resumo})


@app.get("/api/devolucoes/cards")
def api_cards_por_bucket():
    require_login()
    bucket = request.args.get("bucket", "").strip()
    allowed_buckets = {"para_revisao", "para_retirar", "outros_problemas"}
    if bucket not in allowed_buckets:
        return jsonify({"mensagem": f"Bucket invalido. Use um de: {sorted(allowed_buckets)}"}), 400
    with db() as conn:
        rows = conn.execute(
            """
            SELECT claim_id, pedido_id, pack_id, order_ids, bucket, regra,
                   reason_id, motivo_label, produto_nome, produto_imagem,
                   valor_pago, taxa_venda, ml_tipo_logistica,
                   return_status, shipment_status, shipment_destination,
                   mandatory, due_date, date_created, last_updated
            FROM ml_claim_classifications
            WHERE active = 1 AND bucket = ?
            ORDER BY
              CASE WHEN due_date IS NULL OR due_date = '' THEN 1 ELSE 0 END,
              due_date ASC,
              last_updated DESC
            """,
            [bucket],
        ).fetchall()
    cards = []
    for row in rows:
        data = dict(row)
        data["order_ids"] = json.loads(data.get("order_ids") or "[]")
        cards.append(data)
    return jsonify({"bucket": bucket, "total": len(cards), "cards": cards})


@app.get("/api/devolucoes/fila-ml-live")
def api_fila_ml_live():
    require_login()
    try:
        env_values = current_env()
        user_id = env_values.get("ML_USER_ID", "")
        if not env_values.get("ML_CLIENT_ID") or not env_values.get("ML_CLIENT_SECRET") or not user_id:
            return jsonify({"mensagem": "Configure ML_CLIENT_ID, ML_CLIENT_SECRET e ML_USER_ID."}), 400
        return jsonify(ml_live_return_queue(user_id))
    except Exception as exc:
        return jsonify({"mensagem": "Nao foi possivel calcular a fila ao vivo do Mercado Livre", "erro": str(exc)}), 400


@app.get("/api/devolucoes/resumo-financeiro")
def api_resumo_financeiro():
    require_login()
    with db() as conn:
        row = conn.execute(
            """
            SELECT
              COUNT(*) as total_devolucoes,
              SUM(CASE WHEN status = 'aprovado' THEN 1 ELSE 0 END) as total_aprovadas,
              SUM(CASE WHEN status = 'parcial' THEN 1 ELSE 0 END) as total_parciais,
              SUM(CASE WHEN status = 'reprovado' THEN 1 ELSE 0 END) as total_reprovadas,
              COALESCE(SUM(valor_recuperado), 0) as valor_recuperado,
              COALESCE(SUM(valor_perdido), 0) as valor_perdido
            FROM devolucoes
            """
        ).fetchone()
    return jsonify(dict(row))


@app.get("/api/devolucoes/sync-diagnostico")
def api_sync_diagnostico():
    require_login()
    with db() as conn:
        sync_runs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM ml_sync_runs
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
        ]
        raw_counts = [
            dict(row)
            for row in conn.execute(
                """
                SELECT resource_type, COUNT(*) AS total
                FROM ml_raw_payloads
                GROUP BY resource_type
                ORDER BY resource_type
                """
            ).fetchall()
        ]
        diffs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM ml_reconciliation_diffs
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()
        ]
    for run in sync_runs:
        run["detalhes"] = json.loads(run.get("detalhes") or "{}")
    return jsonify({"sync_runs": sync_runs, "raw_counts": raw_counts, "diffs": diffs})


def trace_payload(trace_id: str) -> dict:
    with db() as conn:
        events = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM ml_trace_events
                WHERE trace_id = ?
                ORDER BY id
                """,
                [trace_id],
            ).fetchall()
        ]
        sync_run = None
        if events and events[0].get("sync_run_id"):
            sync_run = conn.execute("SELECT * FROM ml_sync_runs WHERE id = ?", [events[0]["sync_run_id"]]).fetchone()
    for event in events:
        event["details"] = json.loads(event.get("details") or "{}")
    run = dict(sync_run) if sync_run else None
    if run:
        run["detalhes"] = json.loads(run.get("detalhes") or "{}")
    return {"trace_id": trace_id, "sync_run": run, "events": events}


@app.get("/api/devolucoes/sync-trace/ultimo")
def api_sync_trace_ultimo():
    require_login()
    with db() as conn:
        row = conn.execute(
            """
            SELECT trace_id
            FROM ml_trace_events
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return jsonify({"mensagem": "Nenhum trace encontrado.", "trace_id": "", "events": []}), 404
    return jsonify(trace_payload(row["trace_id"]))


@app.get("/api/devolucoes/sync-trace/<trace_id>")
def api_sync_trace(trace_id: str):
    require_login()
    payload = trace_payload(trace_id)
    if not payload["events"]:
        return jsonify({"mensagem": "Trace nao encontrado.", "trace_id": trace_id, "events": []}), 404
    return jsonify(payload)


@app.post("/api/devolucoes/sincronizar-ml")
def api_sincronizar_ml():
    require_login()
    sync_run_id = 0
    trace_id = str(uuid4())
    sync_started = perf_counter()
    try:
        env_values = current_env()
        user_id = env_values.get("ML_USER_ID", "")
        if not env_values.get("ML_CLIENT_ID") or not env_values.get("ML_CLIENT_SECRET") or not user_id:
            return jsonify({"mensagem": "Configure ML_CLIENT_ID, ML_CLIENT_SECRET e ML_USER_ID."}), 400
        sync_run_id = start_ml_sync_run("classification_cache", {"user_id": user_id, "trace_id": trace_id})
        add_ml_trace_event(
            trace_id,
            sync_run_id,
            "sync_start",
            details={
                "tipo": "classification_cache",
                "user_id": user_id,
                "max_pages": env_int("ML_LIVE_QUEUE_MAX_PAGES", 3),
                "closed_pages": env_int("ML_LIVE_QUEUE_CLOSED_PAGES", 1),
                "workers": ml_worker_count("ML_LIVE_QUEUE_WORKERS", 4),
            },
        )
        cache_result = refresh_ml_classification_cache(user_id, sync_run_id, trace_id)
        resumo = cache_result["resumo"]
        resumo["fonte"] = "mercado_livre_cache_classificacao"
        add_ml_trace_event(
            trace_id,
            sync_run_id,
            "summary_database",
            details={
                "total": resumo.get("total"),
                "para_revisao": resumo.get("para_revisao"),
                "para_retirar": resumo.get("para_retirar"),
                "outros_problemas": resumo.get("outros_problemas"),
            },
        )
        finish_ml_sync_run(
            sync_run_id,
            status="success" if not cache_result["erros"] else "partial",
            total_declarado=sum(int(value or 0) for value in cache_result["declarados"].values()),
            total_encontrado=cache_result["inspecionados"],
            total_processado=cache_result["cache_misses"],
            total_erros=len(cache_result["erros"]),
            detalhes={"trace_id": trace_id, **cache_result},
        )
        add_ml_trace_event(
            trace_id,
            sync_run_id,
            "sync_finish",
            status="success" if not cache_result["erros"] else "partial",
            details=cache_result,
            started_at=sync_started,
        )

        return jsonify(
            {
                "mensagem": "Sincronizacao concluida",
                "sync_run_id": sync_run_id,
                "trace_id": trace_id,
                "total_declarado_ml": sum(int(value or 0) for value in cache_result["declarados"].values()),
                "total": resumo["total"],
                "criadas": 0,
                "atualizadas": cache_result["cache_misses"],
                "erros": cache_result["erros"],
                "resumo": resumo,
            }
        )
    except Exception as exc:
        if sync_run_id:
            finish_ml_sync_run(sync_run_id, status="error", total_erros=1, detalhes={"erro": str(exc)})
            add_ml_trace_event(
                trace_id,
                sync_run_id,
                "sync_finish",
                status="error",
                details={"erro": str(exc)},
                started_at=sync_started,
            )
        return jsonify({"mensagem": "Nao foi possivel sincronizar o Mercado Livre", "erro": str(exc)}), 400


@app.post("/api/devolucoes/sincronizar-ml-completo")
def api_sincronizar_ml_completo():
    """Sincroniza TODOS os dados do ML sem filtros (abertos E fechados)"""
    require_login()
    sync_run_id = 0
    try:
        env_values = current_env()
        user_id = env_values.get("ML_USER_ID", "")
        if not env_values.get("ML_CLIENT_ID") or not env_values.get("ML_CLIENT_SECRET") or not user_id:
            return jsonify({"mensagem": "Configure ML_CLIENT_ID, ML_CLIENT_SECRET e ML_USER_ID."}), 400

        sync_run_id = start_ml_sync_run("completo", {"user_id": user_id})
        claims = all_ml_claims(user_id)
        for claim in claims:
            save_ml_raw_payload(sync_run_id, "claim", claim.get("id"), claim, claim.get("id"))
        created = updated = 0
        erros: list[str] = []
        itens_processados: list[dict] = []

        with ThreadPoolExecutor(max_workers=ml_worker_count("ML_SYNC_MANUAL_WORKERS", 4)) as executor:
            futures = {executor.submit(build_ml_devolucao, claim, sync_run_id): claim for claim in claims}
            for future in as_completed(futures):
                try:
                    item = future.result()
                    item["ml_ativo"] = 1
                    itens_processados.append(item)
                except Exception as exc:
                    claim = futures[future]
                    erro = str(exc)
                    erros.append(erro)
                    add_ml_reconciliation_diff(sync_run_id, "processamento_claim_completo", "erro", str(claim.get("id") or ""), erro)

        if itens_processados:
            for item in itens_processados:
                action = upsert_ml_devolucao(item)
                created += 1 if action == "created" else 0
                updated += 1 if action == "updated" else 0

        resumo = resumo_from_database()
        resumo["fonte"] = "mercado_livre_completo"
        finish_ml_sync_run(
            sync_run_id,
            status="success" if not erros else "partial",
            total_declarado=len(claims),
            total_encontrado=len(claims),
            total_processado=len(itens_processados),
            total_erros=len(erros),
            detalhes={"criadas": created, "atualizadas": updated, "erros": erros[:3]},
        )

        return jsonify(
            {
                "mensagem": "Sincronizacao COMPLETA finalizada (todos os dados: abertos + fechados)",
                "sync_run_id": sync_run_id,
                "total_processados": len(claims),
                "criadas": created,
                "atualizadas": updated,
                "erros_encontrados": len(erros),
                "amostra_erros": erros[:3],
                "resumo": resumo,
            }
        )
    except Exception as exc:
        if sync_run_id:
            finish_ml_sync_run(sync_run_id, status="error", total_erros=1, detalhes={"erro": str(exc)})
        return jsonify({"mensagem": "Nao foi possivel sincronizar o Mercado Livre completo", "erro": str(exc)}), 400


@app.get("/api/devolucoes/<int:item_id>")
def api_buscar_devolucao(item_id: int):
    require_login()
    with db() as conn:
        item = conn.execute("SELECT * FROM devolucoes WHERE id = ?", [item_id]).fetchone()
    if not item:
        return jsonify({"mensagem": "Devolucao nao encontrada"}), 404
    return jsonify(dict(item))


@app.patch("/api/devolucoes/<int:item_id>/status")
def api_status(item_id: int):
    require_login()
    data = request.get_json(force=True)
    status = data.get("status")
    if status not in STATUS_PERMITIDOS:
        return jsonify({"mensagem": "Status invalido"}), 400
    with db() as conn:
        item = conn.execute("SELECT * FROM devolucoes WHERE id = ?", [item_id]).fetchone()
        if not item:
            return jsonify({"mensagem": "Devolucao nao encontrada"}), 404
        conn.execute("UPDATE devolucoes SET status = ? WHERE id = ?", [status, item_id])
        conn.execute(
            "INSERT INTO historico_status (devolucao_id, status_anterior, status_novo, data_alteracao) VALUES (?, ?, ?, ?)",
            [item_id, item["status"], status, now_iso()],
        )
        updated = conn.execute("SELECT * FROM devolucoes WHERE id = ?", [item_id]).fetchone()
    return jsonify(dict(updated))


@app.post("/api/devolucoes/<int:item_id>/chegada")
def api_chegada(item_id: int):
    require_login()
    data = request.get_json(force=True)
    resultado = data.get("resultado")
    if resultado not in {"esperado", "divergente"}:
        return jsonify({"mensagem": "Resultado deve ser esperado ou divergente."}), 400
    novo_status = "sem_divergencia" if resultado == "esperado" else "divergencia_encontrada"
    ml_ativo = 0 if resultado == "esperado" else 1
    ml_result = None
    with db() as conn:
        item = conn.execute("SELECT * FROM devolucoes WHERE id = ?", [item_id]).fetchone()
        if not item:
            return jsonify({"mensagem": "Devolucao nao encontrada"}), 404
        if resultado == "esperado" and item["marketplace"] == "Mercado Livre" and item["ml_claim_id"]:
            try:
                ml_result = ml_confirm_return_review_ok(item["ml_claim_id"])
            except Exception as exc:
                return jsonify(
                    {
                        "mensagem": "Nao consegui concluir no Mercado Livre. Nada foi fechado no painel local.",
                        "erro": str(exc),
                    }
                ), 400
        conn.execute(
            "UPDATE devolucoes SET chegada_status = ?, status = ?, requer_acao = ?, ml_ativo = ? WHERE id = ?",
            [resultado, novo_status, 0 if resultado == "esperado" else 1, ml_ativo, item_id],
        )
        conn.execute(
            "INSERT INTO historico_status (devolucao_id, status_anterior, status_novo, data_alteracao) VALUES (?, ?, ?, ?)",
            [item_id, item["status"], novo_status, now_iso()],
        )
        updated = conn.execute("SELECT * FROM devolucoes WHERE id = ?", [item_id]).fetchone()
    payload = dict(updated)
    if ml_result:
        payload["mercado_livre"] = ml_result
    return jsonify(payload)


def gerar_mensagem_mediacao(devolucao: dict, checklist: dict | None, evidencias: list[dict]) -> str:
    checklist = checklist or {}
    problemas = [
        ("Embalagem rasgada/violada", "embalagem_rasgada"),
        ("Produto amassado", "produto_amassado"),
        ("Produto riscado", "produto_riscado"),
        ("Produto quebrado/avariado", "produto_quebrado"),
        ("Produto sujo ou com sinais de uso", "produto_sujo"),
        ("Faltando pecas", "faltando_pecas"),
        ("Faltando acessorios", "faltando_acessorios"),
        ("Produto diferente do pedido", "produto_errado"),
        ("Sem embalagem original", "sem_embalagem_original"),
    ]
    problemas_marcados = [label for label, field in problemas if checklist.get(field)]
    linhas = [
        "Olá, Mercado Livre.",
        "",
        "Recebemos a devolução e identificamos divergência na revisão do produto.",
        f"Pedido: {devolucao.get('pedido_id')}",
        f"Produto: {devolucao.get('produto_nome')}",
        f"Motivo informado pelo comprador: {devolucao.get('motivo_devolucao') or '-'}",
        f"Rastreio: {devolucao.get('codigo_rastreio') or '-'}",
        "",
        "Checklist interno:",
        f"- Produto confere com o pedido: {'sim' if (checklist or {}).get('produto_confere') else 'não'}",
        f"- Embalagem íntegra: {'sim' if (checklist or {}).get('embalagem_integra') else 'não'}",
        f"- Possui sinais de uso: {'sim' if (checklist or {}).get('possui_sinais_de_uso') else 'não'}",
        f"- Item quebrado/avariado: {'sim' if (checklist or {}).get('item_quebrado') else 'não'}",
        f"- Faltando peças/acessórios: {'sim' if (checklist or {}).get('faltando_pecas') else 'não'}",
        f"- Motivo informado confere: {'sim' if (checklist or {}).get('motivo_confere') else 'não'}",
    ]
    if problemas_marcados:
        linhas.append("")
        linhas.append("Divergencias encontradas:")
        linhas.extend([f"- {problema}" for problema in problemas_marcados])
    obs = checklist.get("observacoes")
    if obs:
        linhas.extend(["", f"Observações: {obs}"])
    if evidencias:
        linhas.append("")
        linhas.append("Evidências anexadas no painel interno:")
        for ev in evidencias:
            linhas.append(f"- {ev.get('descricao') or 'Evidência'}: {request.host_url.rstrip('/')}{ev.get('arquivo')}")
    linhas.extend(["", "Solicitamos a mediação/revisão com base nas evidências acima."])
    return "\n".join(linhas)


@app.post("/api/devolucoes/<int:item_id>/mediacao/mensagem")
def api_gerar_mediacao(item_id: int):
    require_login()
    with db() as conn:
        devolucao = row_to_dict(conn.execute("SELECT * FROM devolucoes WHERE id = ?", [item_id]).fetchone())
        if not devolucao:
            return jsonify({"mensagem": "Devolucao nao encontrada"}), 404
        checklist = row_to_dict(conn.execute("SELECT * FROM checklists WHERE devolucao_id = ?", [item_id]).fetchone())
        evidencias = [dict(row) for row in conn.execute("SELECT * FROM evidencias WHERE devolucao_id = ? ORDER BY id DESC", [item_id]).fetchall()]
        mensagem = gerar_mensagem_mediacao(devolucao, checklist, evidencias)
        conn.execute(
            "UPDATE devolucoes SET mediacao_mensagem = ?, status = 'contestacao_aberta' WHERE id = ?",
            [mensagem, item_id],
        )
        conn.execute(
            """
            INSERT INTO contestacoes (
              devolucao_id, tipo_divergencia, descricao, valor_contestado,
              evidencia_ids, texto_contestacao, status, data_abertura
            ) VALUES (?, ?, ?, ?, ?, ?, 'aberta', ?)
            """,
            [
                item_id,
                "mediacao_mercado_livre",
                "Mediação gerada automaticamente com checklist e evidências.",
                float(devolucao.get("valor_produto") or 0),
                json.dumps([ev["id"] for ev in evidencias]),
                mensagem,
                now_iso(),
            ],
        )
    return jsonify({"mensagem": "Mensagem de mediacao gerada", "texto": mensagem})


@app.get("/api/devolucoes/<int:item_id>/historico")
def api_historico(item_id: int):
    require_login()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM historico_status WHERE devolucao_id = ? ORDER BY id DESC",
            [item_id],
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/devolucoes/<int:item_id>/checklist")
def api_get_checklist(item_id: int):
    require_login()
    with db() as conn:
        row = conn.execute("SELECT * FROM checklists WHERE devolucao_id = ?", [item_id]).fetchone()
    return jsonify(row_to_dict(row) or {})


@app.post("/api/devolucoes/<int:item_id>/checklist")
def api_save_checklist(item_id: int):
    require_login()
    data = request.get_json(force=True)
    fields = [
        "produto_confere", "embalagem_integra", "possui_sinais_de_uso",
        "item_quebrado", "faltando_pecas", "motivo_confere",
        "embalagem_rasgada", "produto_amassado", "produto_riscado",
        "produto_quebrado", "produto_sujo", "faltando_acessorios",
        "produto_errado", "sem_embalagem_original",
    ]
    values = [1 if data.get(field) else 0 for field in fields]
    with db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO checklists (
              devolucao_id, produto_confere, embalagem_integra, possui_sinais_de_uso,
              item_quebrado, faltando_pecas, motivo_confere, embalagem_rasgada,
              produto_amassado, produto_riscado, produto_quebrado, produto_sujo,
              faltando_acessorios, produto_errado, sem_embalagem_original,
              observacoes, data_checklist
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [item_id, *values, data.get("observacoes", ""), now_iso()],
        )
        row = conn.execute("SELECT * FROM checklists WHERE devolucao_id = ?", [item_id]).fetchone()
    return jsonify(dict(row)), 201


@app.post("/api/devolucoes/<int:item_id>/checklist/progresso")
def api_salvar_progresso_checklist(item_id: int):
    require_login()
    data = request.get_json(force=True)
    etapa = data.get("etapa", 1)
    conteudo = json.dumps(data.get("conteudo", {}))

    with db() as conn:
        conn.execute(
            "UPDATE devolucoes SET etapa_checklist_atual = ?, conteudo_progresso_checklist = ? WHERE id = ?",
            [etapa, conteudo, item_id]
        )
    return jsonify({"mensagem": "Progresso salvo", "etapa": etapa, "percentual": (etapa / 3) * 100})


@app.get("/api/devolucoes/historico/incompletos")
def api_historico_incompletos():
    require_login()
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, pedido_id, produto_nome, etapa_checklist_atual,
                   ROUND((etapa_checklist_atual / 3.0) * 100) as percentual_conclusao,
                   data_solicitacao
            FROM devolucoes
            WHERE etapa_checklist_atual > 0 AND etapa_checklist_atual < 3
            ORDER BY data_solicitacao DESC
            """
        ).fetchall()
    return jsonify([row_to_dict(row) for row in rows])


@app.get("/api/devolucoes/<int:item_id>/evidencias")
def api_list_evidencias(item_id: int):
    require_login()
    with db() as conn:
        rows = conn.execute("SELECT * FROM evidencias WHERE devolucao_id = ? ORDER BY id DESC", [item_id]).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/devolucoes/<int:item_id>/evidencias/upload")
def api_upload_evidencia(item_id: int):
    require_login()
    uploaded = request.files.get("arquivo")
    if not uploaded:
        return jsonify({"mensagem": "Arquivo obrigatorio"}), 400
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}-{secure_filename(uploaded.filename)}"
    uploaded.save(UPLOAD_DIR / filename)
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO evidencias (devolucao_id, tipo, arquivo, descricao, data_upload) VALUES (?, ?, ?, ?, ?)",
            [item_id, request.form.get("tipo", "arquivo"), f"/uploads/{filename}", request.form.get("descricao", uploaded.filename), now_iso()],
        )
        row = conn.execute("SELECT * FROM evidencias WHERE id = ?", [cur.lastrowid]).fetchone()
    return jsonify(dict(row)), 201


@app.delete("/api/devolucoes/<int:item_id>/evidencias/<int:evidencia_id>")
def api_delete_evidencia(item_id: int, evidencia_id: int):
    require_login()
    with db() as conn:
        row = conn.execute("SELECT * FROM evidencias WHERE id = ? AND devolucao_id = ?", [evidencia_id, item_id]).fetchone()
        if not row:
            return jsonify({"mensagem": "Evidencia nao encontrada"}), 404
        if str(row["arquivo"]).startswith("/uploads/"):
            path = UPLOAD_DIR / str(row["arquivo"]).replace("/uploads/", "")
            if path.exists():
                path.unlink()
        conn.execute("DELETE FROM evidencias WHERE id = ?", [evidencia_id])
    return jsonify({"mensagem": "Evidencia excluida"})


@app.get("/api/devolucoes/<int:item_id>/contestacoes")
def api_list_contestacoes(item_id: int):
    require_login()
    with db() as conn:
        rows = conn.execute("SELECT * FROM contestacoes WHERE devolucao_id = ? ORDER BY id DESC", [item_id]).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["evidencia_ids"] = json.loads(item.get("evidencia_ids") or "[]")
        result.append(item)
    return jsonify(result)


@app.post("/api/devolucoes/<int:item_id>/contestacoes")
def api_create_contestacao(item_id: int):
    require_login()
    data = request.get_json(force=True)
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO contestacoes (
              devolucao_id, tipo_divergencia, descricao, valor_contestado,
              evidencia_ids, texto_contestacao, status, data_abertura
            ) VALUES (?, ?, ?, ?, ?, ?, 'aberta', ?)
            """,
            [
                item_id,
                data.get("tipo_divergencia", ""),
                data.get("descricao", ""),
                float(data.get("valor_contestado") or 0),
                json.dumps(data.get("evidencia_ids") or []),
                data.get("texto_contestacao", ""),
                now_iso(),
            ],
        )
        conn.execute("UPDATE devolucoes SET status = 'contestacao_aberta' WHERE id = ?", [item_id])
        row = conn.execute("SELECT * FROM contestacoes WHERE id = ?", [cur.lastrowid]).fetchone()
    return jsonify(dict(row)), 201


@app.patch("/api/devolucoes/<int:item_id>/contestacoes/<int:contestacao_id>/resultado")
def api_resultado_contestacao(item_id: int, contestacao_id: int):
    require_login()
    data = request.get_json(force=True)
    resultado = data.get("resultado")
    if resultado not in {"aprovado", "parcial", "reprovado"}:
        return jsonify({"mensagem": "Resultado invalido"}), 400
    with db() as conn:
        conn.execute("UPDATE contestacoes SET status = ?, data_resultado = ? WHERE id = ?", [resultado, now_iso(), contestacao_id])
        conn.execute(
            "UPDATE devolucoes SET status = ?, valor_recuperado = ?, valor_perdido = ?, observacao_final = ? WHERE id = ?",
            [resultado, float(data.get("valor_recuperado") or 0), float(data.get("valor_perdido") or 0), data.get("observacao_final", ""), item_id],
        )
    return jsonify({"mensagem": "Resultado salvo"})


if __name__ == "__main__":
    init_database()
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "5000"))
    app.run(host=host, port=port, debug=False)

from __future__ import annotations

import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_devolucoes_ml_claim_id
            ON devolucoes(ml_claim_id)
            WHERE ml_claim_id IS NOT NULL AND ml_claim_id != ''
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
    review_actions = {"return_review_ok", "return_review_fail", "return_review_unified_ok", "return_review_unified_fail"}
    return any(action.get("action") in review_actions for action in claim_available_actions(claim))


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


def build_ml_devolucao(claim: dict) -> dict:
    claim_id = claim.get("id")
    resource_id = claim.get("resource_id")
    retorno = None
    order = None
    try:
        retorno = ml_get(f"/post-purchase/v2/claims/{claim_id}/returns")
    except Exception:
        retorno = None
    if resource_id:
        try:
            order = ml_get(f"/orders/{resource_id}")
        except Exception:
            order = None

    item = ((order or {}).get("order_items") or [{}])[0].get("item", {})
    buyer = (order or {}).get("buyer", {})
    buyer_name = " ".join([buyer.get("first_name") or "", buyer.get("last_name") or ""]).strip()
    shipment = ((retorno or {}).get("shipments") or [(retorno or {}).get("shipment") or {}])[0]
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
        shipment_items = (retorno or {}).get("shipments") or [(retorno or {}).get("shipment") or {}]
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
            shipment = ((retorno or {}).get("shipments") or [(retorno or {}).get("shipment") or {}])[0]
            return {
                "status": str((retorno or {}).get("status") or "").lower(),
                "shipment_status": str((shipment or {}).get("status") or "").lower(),
                "date_created": (retorno or {}).get("date_created") or "",
            }
        except Exception:
            continue
    return {"status": "", "shipment_status": "", "date_created": ""}


def classify_ml_next_claim(claim: dict, return_info: dict | None = None, today_local=None) -> str | None:
    if claim.get("status") != "opened":
        return None
    today_local = today_local or datetime.now().date()
    return_info = return_info or claim_return_info(claim.get("id"))
    return_status = str(return_info.get("status") or "").lower()
    shipment_status = str(return_info.get("shipment_status") or "").lower()
    return_created_today = str(return_info.get("date_created") or "")[:10] == today_local.isoformat()
    reason = claim.get("reason_id") or "sem_motivo"
    updated_value = str(claim.get("last_updated") or claim.get("date_created") or "")
    updated_today = updated_value[:10] == today_local.isoformat()
    review_action = has_return_review_action(claim)

    if review_action or return_status in {"delivered", "received", "failed"} or reason in {"PDD9944", "PDD9968"}:
        claim["_next_kind"] = "revisao"
        claim["_review_due_date"] = review_due_date(claim) or updated_value
        return "revisao"
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
        max_offset = 1000 if status_filter == "opened" else 1200
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


def ml_claims_search(user_id: str, status: str, *, claim_type: str = "returns", max_pages: int = 10) -> tuple[list[dict], int]:
    claims: list[dict] = []
    total = 0
    for page in range(max_pages):
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

    with ThreadPoolExecutor(max_workers=16) as executor:
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


def opened_claims_for_next_attendance(user_id: str) -> list[dict]:
    claims, _ = ml_claims_search(user_id, "opened", max_pages=10)

    def classify(claim: dict) -> tuple[int, str, dict] | None:
        kind = classify_ml_next_claim(claim)
        if not kind:
            return None
        priority = {"revisao": 1, "retirar_correio": 2, "outros_problemas": 3}[kind]
        return (priority, kind, claim)

        """
        Classifica claims em 3 categorias SEM LIMITES:
        1. REVISAO: Produto entregue, aguardando sua decisão
        2. RETIRAR_CORREIO: Full ML com label, aguardando retirada
        3. OUTROS_PROBLEMAS: Tudo que está "opened" mas não é revisão ou retirada
        """
        if claim.get("status") != "opened":
            # Claims fechadas não sincronizam
            return None

        due_date = review_due_date(claim)
        status = claim_return_status(claim.get("id"))
        reason = claim.get("reason_id")

        # CATEGORIA 1: PARA SUA REVISÃO
        # Produto já foi entregue e aguarda sua ação
        if due_date and status == "delivered":
            claim["_next_kind"] = "revisao"
            claim["_review_due_date"] = due_date
            return (1, "revisao", claim)

        # CATEGORIA 2: PARA RETIRAR NO CORREIO
        # Full ML com label gerado (PDD9967 = retirada)
        if status == "label_generated" and reason == "PDD9967":
            claim["_next_kind"] = "retirar_correio"
            return (2, "retirar_correio", claim)

        # CATEGORIA 3: OUTROS PROBLEMAS
        # Qualquer claim aberta que tem label (aguardando envio do comprador)
        # OU sem label mas está em andamento
        if status == "label_generated":
            claim["_next_kind"] = "outros_problemas"
            return (3, "outros_problemas", claim)

        # Qualquer outra devolução aberta sem label
        if status in {"shipped", "processing", "in_return"}:
            claim["_next_kind"] = "outros_problemas"
            return (3, "outros_problemas", claim)

        return None

    classified: list[tuple[int, str, dict]] = []
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(classify, claim) for claim in claims]
        for future in as_completed(futures):
            item = future.result()
            if item:
                classified.append(item)

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

    return revisao + retirar + outros


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
    ]
    item = {**item, "ultima_sincronizacao_ml": now_iso(), "ml_ativo": int(item.get("ml_ativo", 1))}
    with db() as conn:
        if item.get("ml_claim_id"):
            row = conn.execute(
                "SELECT * FROM devolucoes WHERE ml_claim_id = ? OR (marketplace = 'Mercado Livre' AND pedido_id = ?) LIMIT 1",
                [item["ml_claim_id"], item["pedido_id"]],
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
                "SELECT * FROM devolucoes WHERE ml_claim_id = ? OR (marketplace = 'Mercado Livre' AND pedido_id = ?) LIMIT 1",
                [item["ml_claim_id"], item["pedido_id"]],
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
    return jsonify(resumo_from_database())


@app.get("/api/devolucoes/filtros-ml")
def api_filtros_ml():
    require_login()
    try:
        env_values = current_env()
        user_id = env_values.get("ML_USER_ID", "")
        if not env_values.get("ML_CLIENT_ID") or not env_values.get("ML_CLIENT_SECRET") or not user_id:
            return jsonify({"mensagem": "Configure ML_CLIENT_ID, ML_CLIENT_SECRET e ML_USER_ID."}), 400
        return jsonify(post_sales_filters_from_ml(user_id))
    except Exception as exc:
        return jsonify({"mensagem": "Nao foi possivel carregar os filtros do Mercado Livre", "erro": str(exc)}), 400


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


@app.post("/api/devolucoes/sincronizar-ml")
def api_sincronizar_ml():
    require_login()
    try:
        env_values = current_env()
        user_id = env_values.get("ML_USER_ID", "")
        if not env_values.get("ML_CLIENT_ID") or not env_values.get("ML_CLIENT_SECRET") or not user_id:
            return jsonify({"mensagem": "Configure ML_CLIENT_ID, ML_CLIENT_SECRET e ML_USER_ID."}), 400
        claims = opened_claims_for_next_attendance(user_id)
        created = updated = 0
        erros: list[str] = []
        itens_processados: list[dict] = []

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(build_ml_devolucao, claim): claim for claim in claims}
            for future in as_completed(futures):
                try:
                    item = future.result()
                    kind = futures[future].get("_next_kind")
                    item["ml_ativo"] = 1
                    if kind == "retirar_correio":
                        item["prioridade_prazo"] = "retirar_correio"
                        item["requer_acao"] = 0
                        item["acao_recomendada"] = "Devolucao para retirar no correio."
                    elif kind == "revisao":
                        item["status"] = "produto_recebido"
                        item["prioridade_prazo"] = "hoje"
                        item["requer_acao"] = 1
                    elif kind == "outros_problemas":
                        item["prioridade_prazo"] = "outros_problemas"
                        item["requer_acao"] = 0
                        item["acao_recomendada"] = "Devolucao aberta no Mercado Livre, mas fora da fila imediata de revisao."
                    itens_processados.append(item)
                except Exception as exc:
                    erros.append(str(exc))

        if itens_processados or not claims:
            with db() as conn:
                conn.execute("UPDATE devolucoes SET ml_ativo = 0 WHERE marketplace = 'Mercado Livre'")
            for item in itens_processados:
                action = upsert_ml_devolucao(item)
                created += 1 if action == "created" else 0
                updated += 1 if action == "updated" else 0
        resumo = resumo_from_database()
        resumo["fonte"] = "mercado_livre"

        return jsonify(
            {
                "mensagem": "Sincronizacao concluida",
                "total": len(claims),
                "criadas": created,
                "atualizadas": updated,
                "erros": erros[:5],
                "resumo": resumo,
            }
        )
    except Exception as exc:
        return jsonify({"mensagem": "Nao foi possivel sincronizar o Mercado Livre", "erro": str(exc)}), 400


@app.post("/api/devolucoes/sincronizar-ml-completo")
def api_sincronizar_ml_completo():
    """Sincroniza TODOS os dados do ML sem filtros (abertos E fechados)"""
    require_login()
    try:
        env_values = current_env()
        user_id = env_values.get("ML_USER_ID", "")
        if not env_values.get("ML_CLIENT_ID") or not env_values.get("ML_CLIENT_SECRET") or not user_id:
            return jsonify({"mensagem": "Configure ML_CLIENT_ID, ML_CLIENT_SECRET e ML_USER_ID."}), 400

        claims = all_ml_claims(user_id)
        created = updated = 0
        erros: list[str] = []
        itens_processados: list[dict] = []

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {executor.submit(build_ml_devolucao, claim): claim for claim in claims}
            for future in as_completed(futures):
                try:
                    item = future.result()
                    item["ml_ativo"] = 1
                    itens_processados.append(item)
                except Exception as exc:
                    erros.append(str(exc))

        if itens_processados:
            for item in itens_processados:
                action = upsert_ml_devolucao(item)
                created += 1 if action == "created" else 0
                updated += 1 if action == "updated" else 0

        resumo = resumo_from_database()
        resumo["fonte"] = "mercado_livre_completo"

        return jsonify(
            {
                "mensagem": "Sincronizacao COMPLETA finalizada (todos os dados: abertos + fechados)",
                "total_processados": len(claims),
                "criadas": created,
                "atualizadas": updated,
                "erros_encontrados": len(erros),
                "amostra_erros": erros[:3],
                "resumo": resumo,
            }
        )
    except Exception as exc:
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

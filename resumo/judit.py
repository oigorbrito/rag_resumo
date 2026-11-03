"""Cliente mínimo para consultas assíncronas Judit (contrato oficial)."""
from __future__ import annotations

import os
import re
import time
import uuid
from typing import Any

import httpx
from dotenv import load_dotenv

BASE_URL = "https://requests.production.judit.io"
_RE_CNJ = re.compile(r"^[0-9]{7}-[0-9]{2}\.[0-9]{4}\.[0-9]\.[0-9]{2}\.[0-9]{4}$")
_PAGE_SIZE = 100
_MAX_PAGES = 1000


def _cnj_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


class JuditError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, category: str = "api"):
        super().__init__(message)
        self.status_code = status_code
        self.category = category


def carregar_ambiente() -> None:
    """Carrega .env para uso local; variáveis já exportadas têm precedência."""
    load_dotenv(override=False)


class ClienteJudit:
    def __init__(self, api_key: str | None = None, *, cliente: Any = None,
                 base_url: str = BASE_URL, timeout: float = 25):
        carregar_ambiente()
        self.api_key = api_key or os.getenv("JUDIT_API_KEY")
        if not self.api_key:
            raise JuditError("JUDIT_API_KEY ausente", category="configuration")
        self._cliente = cliente or httpx.Client(
            base_url=base_url, timeout=timeout, trust_env=False,
            follow_redirects=False,
            headers={"api-key": self.api_key, "Accept": "application/json"},
        )

    def _get_json(self, path: str, **kwargs) -> dict:
        try:
            response = self._cliente.get(path, **kwargs)
            self._check(response)
            data = response.json()
        except httpx.RequestError as exc:
            raise JuditError(f"Falha de transporte Judit ({type(exc).__name__})", category="transport") from None
        except ValueError:
            raise JuditError("Resposta Judit não contém JSON válido", category="invalid_response") from None
        if not isinstance(data, dict):
            raise JuditError("Resposta Judit tem formato inesperado", category="invalid_response")
        return data

    def _post_json(self, path: str, body: dict) -> dict:
        try:
            response = self._cliente.post(path, json=body)
            self._check(response)
            data = response.json()
        except httpx.RequestError as exc:
            raise JuditError(f"Falha de transporte Judit ({type(exc).__name__})", category="transport") from None
        except ValueError:
            raise JuditError("Resposta Judit não contém JSON válido", category="invalid_response") from None
        if not isinstance(data, dict):
            raise JuditError("Resposta Judit tem formato inesperado", category="invalid_response")
        return data

    @staticmethod
    def _check(response) -> None:
        if response.status_code >= 400:
            code = response.status_code
            category = {401: "authentication", 403: "authorization", 404: "not_found",
                        429: "rate_limit"}.get(code, "http")
            raise JuditError(f"Judit respondeu HTTP {code} ({category})", status_code=code, category=category)

    def criar_consulta(self, cnj: str) -> str:
        if not _RE_CNJ.fullmatch(cnj):
            raise ValueError("CNJ inválido; use o formato NNNNNNN-DD.AAAA.J.TR.OOOO")
        data = self._post_json("/requests", {
            "search": {"search_type": "lawsuit_cnj", "search_key": cnj},
            "with_attachments": False,
        })
        request_id = data.get("request_id")
        try:
            return str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise JuditError("Judit não retornou request_id válido", category="invalid_response") from None

    def aguardar(self, request_id: str, *, timeout: float = 300, intervalo: float = 3) -> None:
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("request-id inválido") from None
        limite = time.monotonic() + timeout
        while True:
            data = self._get_json(f"/requests/{request_id}")
            status = data.get("status")
            if status == "completed":
                return
            if status == "cancelled":
                raise JuditError("Consulta Judit foi cancelada", category="cancelled")
            if status not in {"pending", "started", "cancelling"}:
                raise JuditError("Status desconhecido na resposta Judit", category="invalid_response")
            restante = limite - time.monotonic()
            if restante <= 0:
                raise JuditError("Tempo limite aguardando consulta Judit", category="timeout")
            time.sleep(min(intervalo, restante))

    def recuperar(self, request_id: str) -> dict:
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("request-id inválido") from None
        status = self._get_json(f"/requests/{request_id}")
        if status.get("status") != "completed":
            if status.get("status") == "cancelled":
                raise JuditError("Consulta Judit foi cancelada", category="cancelled")
            raise JuditError("Consulta Judit ainda não está concluída", category="pending")
        if status.get("request_id") and status["request_id"] != request_id:
            raise JuditError("Identidade da consulta não confere", category="identity_mismatch")
        search = status.get("search")
        if not isinstance(search, dict) or search.get("search_type") != "lawsuit_cnj" or not search.get("search_key"):
            raise JuditError("Consulta não contém identidade CNJ verificável", category="invalid_response")
        cnj_esperado = _cnj_digits(search["search_key"])

        escolhidos: dict[tuple[str, int | None], dict] = {}
        for pagina in range(1, _MAX_PAGES + 1):
            data = self._get_json("/responses", params={
                "request_id": request_id, "page": pagina, "page_size": _PAGE_SIZE,
            })
            rows = data.get("page_data")
            if not isinstance(rows, list):
                raise JuditError("page_data não é uma lista", category="invalid_response")
            for item in rows:
                if not isinstance(item, dict):
                    raise JuditError("Item de resposta Judit inválido", category="invalid_response")
                if item.get("request_id") and item["request_id"] != request_id:
                    raise JuditError("Resposta pertence a outro request-id", category="identity_mismatch")
                if item.get("response_type") != "lawsuit":
                    continue
                response_data = item.get("response_data")
                if not isinstance(response_data, dict) or not response_data.get("code"):
                    raise JuditError("lawsuit sem response_data/code válido", category="invalid_response")
                if _cnj_digits(str(response_data["code"])) != cnj_esperado:
                    raise JuditError("CNJ retornado não corresponde à consulta", category="identity_mismatch")
                instance = response_data.get("instance")
                try:
                    instance = int(instance) if instance is not None else None
                except (TypeError, ValueError):
                    raise JuditError("Instância inválida na resposta Judit", category="invalid_response") from None
                key = (cnj_esperado, instance)
                cached = item.get("cached_response", response_data.get("cached_response"))
                rank = 1 if cached is False else -1 if cached is True else 0
                freshness = (rank, str(item.get("created_at") or ""), str(item.get("response_id") or ""))
                previous = escolhidos.get(key)
                if previous is None or freshness > previous["_freshness"]:
                    escolhidos[key] = {**item, "_freshness": freshness}
            if len(rows) < _PAGE_SIZE:
                break
        else:
            raise JuditError(f"Paginação excedeu limite seguro ({_MAX_PAGES} páginas)", category="pagination_limit")

        if not escolhidos:
            raise JuditError("Nenhuma resposta lawsuit encontrada", category="not_found")
        itens = [item for item in escolhidos.values()]
        for item in itens:
            item.pop("_freshness", None)
        itens.sort(key=lambda item: (
            _cnj_digits(item["response_data"]["code"]),
            int(item["response_data"].get("instance") or 0),
            str(item.get("response_id") or ""),
        ))
        return {"page_data": itens}

    def consultar(self, cnj: str, *, timeout: float = 300, intervalo: float = 3) -> tuple[str, dict]:
        request_id = self.criar_consulta(cnj)
        self.aguardar(request_id, timeout=timeout, intervalo=intervalo)
        return request_id, self.recuperar(request_id)

    def close(self) -> None:
        close = getattr(self._cliente, "close", None)
        if close:
            close()

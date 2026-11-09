#!/usr/bin/env python3
"""Recebe webhooks da Judit e gera o resumo de cada request concluído.

    python scripts/servidor_webhook.py --porta 8080 --pasta saidas

Regras da Judit: o webhook não traz token; proteja com restrição de IP (pedido ao suporte)
e exponha só por HTTPS. O servidor responde 200 em segundos e processa em background.
A geração reaproveita scripts/consultar_gerar.py, com os mesmos bloqueios de repetição.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from resumo.judit import ClienteJudit, JuditError  # noqa: E402

ROTA = "/judit/webhook"
TAMANHO_MAX = 1_000_000
_LOCK = threading.Lock()
_EM_ANDAMENTO: set[str] = set()


def _ja_processado(rid: str, pasta: Path) -> bool:
    return (pasta / f"{rid}.json").exists() or (pasta / f"{rid}.md").exists()


def _processar(rid: str, pasta: Path) -> None:
    try:
        ClienteJudit().aguardar(rid, timeout=900, intervalo=5)
    except JuditError as exc:
        print(f"[{rid}] Judit não concluiu: {exc.category}", file=sys.stderr, flush=True)
    else:
        resultado = subprocess.run([
            sys.executable, str(ROOT / "scripts" / "consultar_gerar.py"),
            "--request-id", rid,
            "--saida", str(pasta / f"{rid}.md"),
            "--relatorio", str(pasta / f"{rid}.json"),
        ], check=False)
        print(f"[{rid}] geração terminou com exit={resultado.returncode}", flush=True)
    finally:
        with _LOCK:
            _EM_ANDAMENTO.discard(rid)


def tratar_evento(evento, pasta: Path, disparar=_processar) -> tuple[int, dict]:
    """Decide o que fazer com um evento. Retorna (status HTTP, corpo)."""
    if not isinstance(evento, dict):
        return 400, {"erro": "corpo inválido"}
    if evento.get("event_type") != "response_created":
        return 200, {"status": "ignorado"}
    if evento.get("reference_type") not in (None, "request"):
        return 200, {"status": "ignorado"}
    try:
        rid = str(uuid.UUID(str(evento.get("reference_id"))))
    except (ValueError, TypeError):
        return 400, {"erro": "reference_id inválido"}
    with _LOCK:
        if rid in _EM_ANDAMENTO or _ja_processado(rid, pasta):
            return 200, {"status": "duplicado", "request_id": rid}
        _EM_ANDAMENTO.add(rid)
    threading.Thread(target=disparar, args=(rid, pasta), daemon=True).start()
    return 200, {"status": "aceito", "request_id": rid}


class Handler(BaseHTTPRequestHandler):
    pasta: Path = Path("saidas")

    def do_POST(self):  # noqa: N802
        if self.path != ROTA:
            return self._responder(404, {"erro": "rota inexistente"})
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._responder(400, {"erro": "Content-Length inválido"})
        if not 0 < tamanho <= TAMANHO_MAX:
            return self._responder(400, {"erro": "tamanho inválido"})
        try:
            evento = json.loads(self.rfile.read(tamanho).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._responder(400, {"erro": "JSON inválido"})
        status, corpo = tratar_evento(evento, self.pasta)
        self._responder(status, corpo)

    def _responder(self, status: int, corpo: dict) -> None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, fmt, *args):  # não registra o corpo (dados de processo)
        print(f"{self.address_string()} {fmt % args}", file=sys.stderr, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--porta", type=int, default=8080)
    ap.add_argument("--pasta", type=Path, default=ROOT / "saidas")
    a = ap.parse_args()
    a.pasta.mkdir(parents=True, exist_ok=True)
    Handler.pasta = a.pasta
    print(f"Escutando em http://{a.host}:{a.porta}{ROTA} (saídas em {a.pasta})", flush=True)
    ThreadingHTTPServer((a.host, a.porta), Handler).serve_forever()


if __name__ == "__main__":
    main()

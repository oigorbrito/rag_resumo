#!/usr/bin/env python3
"""Prova de instalação do zero: copia o projeto para uma pasta limpa, instala as dependências num ambiente
novo, roda os testes e abre o programa. Não usa chaves reais e não faz nenhuma consulta paga.

    python scripts/verificar_instalacao.py

Opções para desenvolvimento: --sem-venv (usa o Python atual), --sem-testes, --manter (não apaga a pasta temporária).
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IGNORAR = shutil.ignore_patterns(".git", ".venv", "venv", "indice", "dados", "saidas", "_to_delete",
                                 "__pycache__", ".pytest_cache", ".env", "relatorio*.json", "resumo*.md",
                                 "summary_ia.md", "*.log", "*.bak")
resultados: list[bool] = []
_SEM_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def passo(nome: str, ok: bool, detalhe: str = "") -> bool:
    resultados.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nome + (f" | {detalhe}" if detalhe else ""), flush=True)
    return bool(ok)


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(url: str, dados: bytes | None = None):
    try:
        with _SEM_PROXY.open(urllib.request.Request(url, data=dados), timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return None, ""


def _fim() -> int:
    ok = sum(resultados)
    print(f"\nRESUMO: {ok}/{len(resultados)} PASS, {len(resultados) - ok} FAIL", flush=True)
    return 0 if ok == len(resultados) and resultados else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sem-venv", action="store_true")
    ap.add_argument("--sem-testes", action="store_true")
    ap.add_argument("--manter", action="store_true")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="rag_resumo_limpo_"))
    proc = None
    log = None
    try:
        destino = tmp / "projeto"
        shutil.copytree(ROOT, destino, ignore=IGNORAR)
        passo("cópia limpa (sem .env, índice, dados nem ambiente virtual)",
              not any((destino / n).exists() for n in (".env", "indice", "dados", ".venv")))

        if a.sem_venv:
            py = sys.executable
        else:
            venv.create(tmp / ".venv", with_pip=True)
            py = str(tmp / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
            r = subprocess.run([py, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
                               cwd=destino, capture_output=True, text=True)
            passo("instalação das dependências em ambiente novo", r.returncode == 0,
                  "" if r.returncode == 0 else (r.stderr or r.stdout)[-300:])
            if r.returncode != 0:
                return _fim()

        if not a.sem_testes:
            r = subprocess.run([py, "-m", "pytest", "-q"], cwd=destino, capture_output=True, text=True)
            ultima = (r.stdout.strip().splitlines() or ["sem saída"])[-1]
            passo("testes automáticos na cópia limpa", r.returncode == 0,
                  ultima if r.returncode == 0 else "\n" + (r.stdout + r.stderr)[-900:])

        porta = _porta_livre()
        env = {k: v for k, v in os.environ.items() if k != "APP_SENHA"}
        env.update(ANTHROPIC_API_KEY="chave-falsa-de-teste", JUDIT_API_KEY="chave-falsa-de-teste",
                   HOST="127.0.0.1", PORT=str(porta), RESUMO_DADOS_DIR=str(tmp / "dados"), PYTHONUTF8="1")
        log = open(tmp / "servidor.log", "w", encoding="utf-8")
        proc = subprocess.Popen([py, "scripts/app_web.py"], cwd=destino, env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{porta}"
        status, corpo = None, ""
        limite = time.time() + 90
        while time.time() < limite and proc.poll() is None:
            status, corpo = _http(base + "/")
            if status == 200:
                break
            time.sleep(1)
        log.flush()
        trecho = (tmp / "servidor.log").read_text(encoding="utf-8", errors="replace")[-300:]
        passo("índice do glossário criado automaticamente", (destino / "indice" / "kb.json").exists())
        passo("programa abre a página inicial", status == 200 and "Resumo de processo" in corpo,
              "" if status == 200 else trecho)
        status, _ = _http(base + "/gerar", b"cnj=123")
        passo("CNJ inválido é recusado sem consulta paga", status == 400)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if log is not None:
            log.close()
        if a.manter:
            print(f"Pasta temporária mantida em {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return _fim()


if __name__ == "__main__":
    raise SystemExit(main())

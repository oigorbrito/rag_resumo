#!/usr/bin/env python3
"""Busca um processo público no DataJud e gera summary_ia pelo pipeline local.

Uso: python scripts/gerar_datajud.py <CNJ> --saida resumo_datajud.md
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import httpx
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from resumo.generator import GeradorTemplate  # noqa: E402
from resumo.knowledge import BaseConhecimento  # noqa: E402
from resumo.parser import extrair_fatos, para_local, fmt_data, norm  # noqa: E402
from resumo.pipeline import gerar_resumos  # noqa: E402

WIKI_ACESSO = "https://datajud-wiki.cnj.jus.br/api-publica/acesso"
URL_TRF1 = "https://api-publica.datajud.cnj.jus.br/api_publica_trf1/_search"


class _TextoHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.partes: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.partes.append(data.strip())


def chave_publica() -> str:
    """Usa DATAJUD_API_KEY ou obtém a chave corrente da página oficial do CNJ."""
    load_dotenv(RAIZ / ".env", override=False)
    configurada = os.getenv("DATAJUD_API_KEY")
    if configurada:
        return configurada
    try:
        resposta = httpx.get(WIKI_ACESSO, timeout=25, trust_env=False, follow_redirects=True)
    except httpx.RequestError as exc:
        raise RuntimeError(f"Não foi possível acessar a documentação do CNJ ({type(exc).__name__}).") from None
    if resposta.status_code != 200:
        raise RuntimeError(f"Documentação do CNJ retornou HTTP {resposta.status_code}.")
    parser = _TextoHTML()
    parser.feed(resposta.text)
    m = re.search(r"APIKey\s+atual\s*:\s*Authorization\s*:\s*APIKey\s+([A-Za-z0-9+/=]+)",
                  " ".join(parser.partes), re.I)
    if not m:
        raise RuntimeError("Chave pública vigente não localizada na documentação oficial do CNJ.")
    return m.group(1)


def _data_iso(valor):
    """Normaliza datas compactas do Elasticsearch sem descartar a hora."""
    if not isinstance(valor, str):
        return valor
    compacta = re.fullmatch(r"(\d{8})(\d{6})?", valor)
    if compacta:
        fmt = "%Y%m%d%H%M%S" if compacta.group(2) else "%Y%m%d"
        dt = datetime.strptime(valor, fmt).replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    return valor


def _assuntos(itens) -> list[dict]:
    saida = []
    for item in itens or []:
        if isinstance(item, list):
            saida.extend(_assuntos(item))
        elif isinstance(item, dict) and item.get("nome"):
            saida.append({"code": item.get("codigo"), "name": item["nome"]})
    return saida


def formatar_cnj(numero: str) -> str:
    digitos = re.sub(r"\D", "", numero or "")
    if len(digitos) != 20:
        raise ValueError("O número do processo deve conter 20 dígitos CNJ.")
    return f"{digitos[:7]}-{digitos[7:9]}.{digitos[9:13]}.{digitos[13]}.{digitos[14:16]}.{digitos[16:]}"


def converter_datajud(fonte: dict) -> dict:
    """Mapeia apenas campos existentes no DataJud para o contrato Judit do parser."""
    cnj = formatar_cnj(fonte.get("numeroProcesso", ""))
    classe = fonte.get("classe") or {}
    movimentos = fonte.get("movimentos") or []
    steps = [{"content": m["nome"], "step_date": _data_iso(m.get("dataHora")), "private": False}
             for m in movimentos if isinstance(m, dict) and m.get("nome")]
    distribuicao = next((_data_iso(m.get("dataHora")) for m in movimentos
                         if isinstance(m, dict) and "DISTRIBUI" in norm(m.get("nome"))), None)
    orgao = fonte.get("orgaoJulgador") or {}
    dados = {
        "code": cnj,
        "tribunal_acronym": fonte.get("tribunal"),
        "filing_date": _data_iso(fonte.get("dataAjuizamento")),
        "distribution_date": distribuicao,
        "classifications": ([{"code": classe.get("codigo"), "name": classe["nome"]}]
                             if classe.get("nome") else []),
        "subjects": _assuntos(fonte.get("assuntos")),
        "courts": ([{"name": orgao["nome"]}] if orgao.get("nome") else []),
        "steps": steps,
        "secrecy_level": fonte.get("nivelSigilo"),
        # DataJud não fornece partes neste documento; não sintetizar litigantes ou patronos.
    }
    return {"response_type": "lawsuit", "response_data": dados}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cnj", help="número CNJ com ou sem pontuação")
    ap.add_argument("--indice", default=str(RAIZ / "indice" / "kb"))
    ap.add_argument("--saida", default="resumo_datajud.md")
    args = ap.parse_args()
    numero = re.sub(r"\D", "", args.cnj)
    formatar_cnj(numero)
    try:
        key = chave_publica()
        resposta = httpx.post(
            URL_TRF1,
            headers={"Authorization": f"APIKey {key}", "Content-Type": "application/json"},
            json={"query": {"match": {"numeroProcesso": numero}}},
            timeout=90, trust_env=False, follow_redirects=False,
        )
    except httpx.TimeoutException:
        raise SystemExit("DataJud: timeout; nenhuma resposta HTTP recebida.") from None
    except httpx.RequestError as exc:
        raise SystemExit(f"DataJud: erro de transporte ({type(exc).__name__}).") from None
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    if resposta.status_code != 200:
        raise SystemExit(f"DataJud retornou HTTP {resposta.status_code}; resposta não registrada.")
    try:
        candidatos = resposta.json().get("hits", {}).get("hits", [])
    except (ValueError, AttributeError):
        raise SystemExit("DataJud retornou JSON inválido.") from None
    source = next((h.get("_source", {}) for h in candidatos
                   if re.sub(r"\D", "", (h.get("_source", {}).get("numeroProcesso") or "")) == numero), None)
    if source is None:
        raise SystemExit("Consulta concluída, mas o processo não foi encontrado no TRF1.")
    payload = converter_datajud(source)
    fatos = extrair_fatos(payload["response_data"])
    expected = Counter((norm(m.get("nome")), fmt_data(para_local(m.get("dataHora"))))
                       for m in source.get("movimentos", []) if m.get("nome"))
    observed = Counter((norm(m.texto), m.data) for m in fatos.movimentos)
    if expected != observed or fatos.cnj != formatar_cnj(numero):
        raise SystemExit("Verificação de CNJ/movimentações contra a resposta DataJud falhou.")
    indice = Path(args.indice)
    if not indice.with_suffix(".json").exists():
        raise SystemExit(f"Índice RAG não encontrado em {indice}; rode scripts/indexar.py.")
    kb = BaseConhecimento.carregar(indice)
    resultados = gerar_resumos(payload, kb, GeradorTemplate())
    if not resultados:
        raise SystemExit("Nenhum resumo elegível foi produzido (verifique sigilo de justiça).")
    resultado = resultados[0]
    if resultado.cnj != formatar_cnj(numero):
        raise SystemExit("CNJ do summary_ia não confere com a fonte DataJud.")
    Path(args.saida).write_text(resultado.summary_ia, encoding="utf-8")
    print(f"DataJud HTTP 200; parser: {len(fatos.movimentos)} movimentações conferidas; "
          f"RAG: {len(resultado.fontes)} fontes; summary_ia: {args.saida}")


if __name__ == "__main__":
    main()

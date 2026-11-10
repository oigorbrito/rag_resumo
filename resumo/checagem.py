"""Checagem determinística de fidelidade: datas e valores do resumo devem existir nos dados da Judit.

Não substitui revisão humana. Pega o caso mais comum de alucinação numérica: data ou
R$ escrito no texto que não aparece em nenhum campo do JSON de origem.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

_RE_DATA_TEXTO = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
_RE_DATA_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})")
_RE_DATA_BR = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
_RE_MOEDA = re.compile(r"R\$\s?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)")
_RE_NUM_STR = re.compile(r"\d[\d.,]*\d|\d")


def _moeda_para_float(token: str) -> float | None:
    try:
        return float(token.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _datas_nos_dados(payload) -> set[str]:
    bruto = json.dumps(payload, ensure_ascii=False)
    datas = set()
    for d, m, a in _RE_DATA_BR.findall(bruto):
        datas.add(f"{d}/{m}/{a}")
    for a, m, d in _RE_DATA_ISO.findall(bruto):
        try:
            datas.add(datetime(int(a), int(m), int(d)).strftime("%d/%m/%Y"))
        except ValueError:
            pass
    return datas


def _valores_nos_dados(payload) -> list[float]:
    valores: list[float] = []

    def visita(x):
        if isinstance(x, dict):
            for v in x.values():
                visita(v)
        elif isinstance(x, list):
            for v in x:
                visita(v)
        elif isinstance(x, bool):
            return
        elif isinstance(x, (int, float)):
            valores.append(float(x))
        elif isinstance(x, str):
            for tok in _RE_NUM_STR.findall(x):
                for cand in (_moeda_para_float(tok), _us_para_float(tok)):
                    if cand is not None:
                        valores.append(cand)

    visita(payload)
    return valores


def _us_para_float(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def verificar(texto: str, payload, fatos: list[dict] | None = None) -> list[str]:
    """Retorna divergências. A fonte de verdade é o JSON bruto da Judit e os fatos extraídos pelo parser
    (que incluem datas derivadas, como fim de prazo, e são o que o modelo recebe)."""
    erros = []
    fonte = {"payload": payload, "fatos": fatos or []}
    datas = _datas_nos_dados(fonte)
    for d, m, a in _RE_DATA_TEXTO.findall(texto):
        if f"{d}/{m}/{a}" not in datas:
            erros.append(f"data {d}/{m}/{a} do resumo não aparece nos dados da Judit")
    valores = _valores_nos_dados(fonte)
    for tok in _RE_MOEDA.findall(texto):
        alvo = _moeda_para_float(tok)
        if alvo is None:
            continue
        if not any(abs(v - alvo) < 0.005 for v in valores):
            erros.append(f"valor R$ {tok} do resumo não aparece nos dados da Judit")
    return sorted(set(erros))


# --------------------------------------------------------------------------
# Verificação item a item (para o revisor humano ver o que foi encontrado e onde)
# --------------------------------------------------------------------------

def _datas_dos_movimentos(payload) -> dict[str, list[str]]:
    """Mapeia data (dd/mm/aaaa, fuso local) -> descrições dos movimentos/campos do processo daquela data."""
    from .parser import extrair_processos, fmt_data, para_local

    mapa: dict[str, list[str]] = {}

    def add(iso, rotulo: str) -> None:
        try:
            dt = para_local(iso) if isinstance(iso, str) else None
            data = fmt_data(dt) if dt else None
        except Exception:
            data = None
        if data:
            mapa.setdefault(data, []).append(rotulo)

    try:
        processos = extrair_processos(payload) if isinstance(payload, dict) else []
    except Exception:
        processos = []
    for proc in processos:
        add(proc.get("distribution_date"), "Data de distribuição do processo")
        add(proc.get("filing_date"), "Data de ajuizamento do processo")
        for s in proc.get("steps") or []:
            if s.get("private"):
                continue
            conteudo = re.sub(r"\s+", " ", s.get("content") or "").strip()
            corte = conteudo[:140] + ("…" if len(conteudo) > 140 else "")
            add(s.get("step_date"), "Movimento: " + corte)
            for d, m, a in _RE_DATA_BR.findall(conteudo):  # datas citadas dentro do texto do movimento
                citada = f"{d}/{m}/{a}"
                if citada not in mapa or not any(r.startswith("Movimento") for r in mapa[citada]):
                    mapa.setdefault(citada, []).append("Movimento (cita a data): " + corte)
    return mapa


def _valor_da_causa(payload) -> float | None:
    from .parser import extrair_processos

    try:
        for proc in extrair_processos(payload) if isinstance(payload, dict) else []:
            v = proc.get("amount")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    except Exception:
        pass
    return None


def itens_verificados(texto: str, payload, fatos: list[dict] | None = None) -> list[dict]:
    """Lista cada data e cada valor em R$ do texto, na ordem em que aparecem, com a situação e a evidência.

    status: "confirmado" (encontrado nos dados da Judit), "derivado" (calculado pelo sistema a partir
    dos dados, como fim de prazo) ou "nao_encontrado". "Confirmado" significa que o número existe nos
    dados; para datas, a evidência mostra o movimento correspondente para o revisor conferir se o
    texto atribuiu a data ao fato certo.
    """
    por_movimento = _datas_dos_movimentos(payload)
    datas_processo = _datas_nos_dados(payload)
    datas_fatos = _datas_nos_dados({"fatos": fatos or []})
    valores_processo = _valores_nos_dados(payload)
    valores_fatos = _valores_nos_dados({"fatos": fatos or []})
    valor_causa = _valor_da_causa(payload)

    achados = [(m.start(), "data", m) for m in _RE_DATA_TEXTO.finditer(texto)]
    achados += [(m.start(), "valor", m) for m in _RE_MOEDA.finditer(texto)]

    itens: list[dict] = []
    vistos: set[str] = set()
    for _, tipo, m in sorted(achados, key=lambda x: x[0]):
        token = m.group(0)
        if token in vistos:
            continue
        vistos.add(token)
        if tipo == "data":
            if token in por_movimento:
                rotulos = por_movimento[token]
                evid = " · ".join(rotulos[:2]) + (f" (+{len(rotulos) - 2})" if len(rotulos) > 2 else "")
                status = "confirmado"
            elif token in datas_processo:
                status, evid = "confirmado", "Data presente nos dados do processo (movimento não identificado)"
            elif token in datas_fatos:
                status, evid = "derivado", "Calculada pelo sistema a partir de dados do processo (ex.: fim de prazo)"
            else:
                status, evid = "nao_encontrado", "Não aparece nos dados do processo"
        else:
            alvo = _moeda_para_float(m.group(1))
            if alvo is None:
                continue
            if valor_causa is not None and abs(valor_causa - alvo) < 0.005:
                status, evid = "confirmado", "Valor da causa informado no processo"
            elif any(abs(v - alvo) < 0.005 for v in valores_processo):
                status, evid = "confirmado", "Valor presente nos dados do processo (campo não identificado)"
            elif any(abs(v - alvo) < 0.005 for v in valores_fatos):
                status, evid = "derivado", "Calculado pelo sistema a partir de dados do processo"
            else:
                status, evid = "nao_encontrado", "Não aparece nos dados do processo"
        itens.append({"tipo": tipo, "texto": token, "status": status, "evidencia": evid})
    return itens

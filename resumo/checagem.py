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

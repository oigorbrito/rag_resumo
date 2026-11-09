"""Seções faltantes do Claude devem falhar a geração, não ser misturadas com template."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from resumo.generator import GeradorClaude, GeradorTemplate, completar
from resumo.knowledge import BaseConhecimento
from resumo.parser import extrair_fatos, extrair_processos

ROOT = Path(__file__).resolve().parents[1]


def _fatos_e_ctx():
    payload = json.loads((ROOT / "tests/fixtures/judit_exemplo.json").read_text(encoding="utf-8"))
    fatos = extrair_fatos(extrair_processos(payload)[0])
    return fatos, {}


def _cliente(entrada: dict):
    bloco = SimpleNamespace(type="tool_use", name="registrar_resumo", input=entrada)
    resp = SimpleNamespace(content=[bloco], stop_reason="tool_use")
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))


def test_completar_reporta_faltantes():
    fatos, ctx = _fatos_e_ctx()
    padrao = GeradorTemplate().gerar(fatos, ctx)
    _, faltantes = completar({"introducao": [], "movimentacoes": ""}, padrao)
    assert "introducao" in faltantes and "movimentacoes" in faltantes


def test_claude_incompleto_falha():
    fatos, ctx = _fatos_e_ctx()
    gerador = GeradorClaude(modelo="teste", client=_cliente({"introducao": ["Texto."], "movimentacoes": "Texto."}))
    with pytest.raises(RuntimeError, match="seções obrigatórias"):
        gerador.gerar(fatos, ctx)


def test_claude_completo_passa():
    fatos, ctx = _fatos_e_ctx()
    padrao = GeradorTemplate().gerar(fatos, ctx)
    entrada = {
        "introducao": ["Texto da introdução."],
        "movimentacoes": "Texto das movimentações.",
        "partes": [{"id": p["id"], "descricao": "Descrição."} for p in padrao["partes"]],
        "classes": [{"id": c["id"], "explicacao": "Explicação."} for c in padrao["classes"]],
        "assuntos": [{"id": a["id"], "explicacao": "Explicação."} for a in padrao["assuntos"]],
    }
    out = GeradorClaude(modelo="teste", client=_cliente(entrada)).gerar(fatos, ctx)
    assert out["introducao"] == ["Texto da introdução."]


def test_resposta_cortada_por_max_tokens_falha():
    fatos, ctx = _fatos_e_ctx()
    bloco = SimpleNamespace(type="tool_use", name="registrar_resumo", input={"introducao": ["x"]})
    resp = SimpleNamespace(content=[bloco], stop_reason="max_tokens")
    cli = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
    with pytest.raises(RuntimeError, match="cortada pelo limite"):
        GeradorClaude(modelo="teste", client=cli).gerar(fatos, ctx)

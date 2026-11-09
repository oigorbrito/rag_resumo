"""Orquestra: JSON da Judit -> fatos -> contexto (RAG) -> textos (Claude) -> markup."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .generator import GeradorClaude, GeradorTemplate
from .knowledge import BaseConhecimento
from .parser import extrair_fatos, extrair_processos
from .render import renderizar

log = logging.getLogger(__name__)


@dataclass
class ResultadoResumo:
    cnj: str
    instancia: int | None
    summary_ia: str
    gerador: str
    alertas: list[str] = field(default_factory=list)
    fontes: list[str] = field(default_factory=list)  # ids da base usados no contexto
    usou_fallback: bool = False
    origin_id: str | None = None  # response_id do item "lawsuit" de origem


def gerar_resumos(payload: dict, kb: BaseConhecimento, gerador=None,
                  permitir_segredo: bool = False, fallback_template: bool = True) -> list[ResultadoResumo]:
    gerador = gerador or GeradorClaude()
    resultados = []
    if "page_data" in payload:
        itens = [(i.get("response_id"), i["response_data"]) for i in payload["page_data"]
                 if i.get("response_type") == "lawsuit"]
    else:
        itens = [(payload.get("response_id"), d) for d in extrair_processos(payload)]
    for origin_id, dados in itens:
        fatos = extrair_fatos(dados)
        if fatos.segredo_justica and not permitir_segredo:
            log.warning("Processo %s em segredo de justiça: resumo não gerado.", fatos.cnj)
            continue
        ctx = kb.contexto_para(fatos)
        usou_fallback = False
        try:
            secoes = gerador.gerar(fatos, ctx)
            nome = gerador.nome
        except Exception as e:  # noqa: BLE001
            if not fallback_template:
                raise
            log.exception("Falha no gerador %s (%s); usando template.", getattr(gerador, "nome", "?"), e)
            secoes = GeradorTemplate().gerar(fatos, ctx)
            nome, usou_fallback = "template", True
        fontes = sorted({t.id for trechos in ctx.values() for t in trechos})
        resultados.append(ResultadoResumo(
            cnj=fatos.cnj, instancia=fatos.instancia, summary_ia=renderizar(fatos, secoes),
            gerador=nome, alertas=fatos.alertas, fontes=fontes, usou_fallback=usou_fallback,
            origin_id=origin_id))
    return resultados


def como_item_judit(res: ResultadoResumo) -> dict:
    """Empacota no mesmo formato do item response_type == "summary" da Judit."""
    agora = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "response_type": "summary",
        "response_data": {"data": [res.summary_ia], "origin_id": res.origin_id, "origin": "response"},
        "tags": {"gerador": res.gerador, "alertas": res.alertas, "fontes": res.fontes},
        "created_at": agora,
    }

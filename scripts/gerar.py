#!/usr/bin/env python3
"""Gera o summary_ia a partir de um JSON da Judit.

    python scripts/gerar.py processo.json                  # Claude (precisa ANTHROPIC_API_KEY)
    python scripts/gerar.py processo.json --sem-llm        # só template, sem API
    python scripts/gerar.py processo.json --mostrar-prompt # imprime o prompt e sai
    python scripts/gerar.py processo.json --formato judit  # saída no formato do item "summary"
"""
import argparse
import json
import logging
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from resumo.generator import MODELO_PADRAO, GeradorClaude, GeradorTemplate  # noqa: E402
from resumo.knowledge import BaseConhecimento  # noqa: E402
from resumo.parser import extrair_fatos, extrair_processos  # noqa: E402
from resumo.pipeline import como_item_judit, gerar_resumos  # noqa: E402
from resumo.prompt import SYSTEM_PROMPT, montar_mensagem  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entrada", help="arquivo JSON da Judit")
    ap.add_argument("--indice", default=str(RAIZ / "indice" / "kb"))
    ap.add_argument("--modelo", default=MODELO_PADRAO)
    ap.add_argument("--sem-llm", action="store_true", help="usa apenas o gerador por template")
    ap.add_argument("--exigir-claude", action="store_true",
                    help="homologação: falha em vez de usar template se Claude não responder")
    ap.add_argument("--mostrar-prompt", action="store_true")
    ap.add_argument("--formato", choices=["md", "judit", "json"], default="md")
    ap.add_argument("--saida", help="arquivo de saída (padrão: stdout)")
    a = ap.parse_args()
    if a.sem_llm and a.exigir_claude:
        ap.error("--exigir-claude não pode ser usado com --sem-llm")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    payload = json.loads(Path(a.entrada).read_text(encoding="utf-8"))
    indice = Path(a.indice)
    if not indice.with_suffix(".json").exists():
        sys.exit(f"Índice não encontrado em {indice}. Rode: python scripts/indexar.py")
    kb = BaseConhecimento.carregar(indice)

    if a.mostrar_prompt:
        for dados in extrair_processos(payload):
            fatos = extrair_fatos(dados)
            print("=== SYSTEM ===\n" + SYSTEM_PROMPT)
            print("=== USER ===\n" + montar_mensagem(fatos, kb.contexto_para(fatos)))
        return

    gerador = GeradorTemplate() if a.sem_llm else GeradorClaude(modelo=a.modelo)
    try:
        resultados = gerar_resumos(payload, kb, gerador, fallback_template=not a.exigir_claude)
    except Exception as exc:
        # Não imprimir exceção completa: pode incluir dados do prompt/processo.
        http = getattr(exc, "status_code", None)
        logging.error("Claude falhou: %s%s; nenhum resumo publicado.",
                      type(exc).__name__, f" (HTTP {http})" if http is not None else "")
        sys.exit(1)
    if not resultados:
        sys.exit("Nenhum processo elegível foi encontrado; saída não gerada.")
    if a.exigir_claude and any(r.usou_fallback or r.gerador == "template" for r in resultados):
        sys.exit("Falha: resultado não foi gerado pelo Claude.")

    if a.formato == "md":
        texto = "\n\n---\n\n".join(r.summary_ia for r in resultados)
    elif a.formato == "judit":
        texto = json.dumps([como_item_judit(r) for r in resultados], ensure_ascii=False, indent=2)
    else:
        texto = json.dumps([r.__dict__ for r in resultados], ensure_ascii=False, indent=2)

    for r in resultados:
        for al in r.alertas:
            logging.warning("[%s] %s", r.cnj, al)

    if a.saida:
        Path(a.saida).write_text(texto, encoding="utf-8")
        logging.info("Resumo salvo em %s", a.saida)
    else:
        print(texto)


if __name__ == "__main__":
    main()

"""Prompt e schema de saída para o Claude.

O LLM NÃO escreve o markup inteiro: ele devolve só os textos (via tool use
forçado) e o render.py monta o layout exatamente como o summary_ia original.
Assim o formato nunca quebra e dados sensíveis (CPF/CNPJ) não passam pelo modelo.
"""
from __future__ import annotations

import json

from .knowledge import Trecho
from .parser import Fatos

SYSTEM_PROMPT = """Você escreve resumos de processos judiciais brasileiros para leigos (clientes, não advogados).

## Tarefa
Recebe FATOS estruturados de um processo e um CONTEXTO recuperado de uma base de conhecimento jurídica. Produz os textos de um resumo chamando a ferramenta `registrar_resumo`.

## Estilo
- Português do Brasil, linguagem simples, tom neutro e informativo, terceira pessoa.
- Explique termos técnicos em palavras do dia a dia ("Em termos simples, ...", "Na prática, ...").
- Nomes de partes em MAIÚSCULAS, exatamente como nos FATOS.
- Datas no formato dd/mm/aaaa; valores como "R$ 30.000,00".

## Marcação permitida dentro dos textos (somente estas)
- <strong>…</strong> para destacar conceitos-chave (tribunal, comarca, fase, situação, pedidos, valor, atos importantes).
- <strong className="text-primary">…</strong> apenas para o número do processo.
- <span className="font-bold">…</span> para nomes de partes citados na introdução e nas movimentações.
- <span className="text-primary">…</span> para datas relevantes.
Nenhuma outra tag, nenhum Markdown, nenhuma quebra de linha dentro de um texto.

## Regras de fidelidade (obrigatórias)
1. Use SOMENTE informações presentes nos FATOS e no CONTEXTO. Nunca invente fatos, pedidos, motivos, decisões, valores ou datas.
2. Não especule sobre o mérito nem sobre quem tem razão. Sobre o que os autores alegam, use formulações cautelosas ("afirma ter sofrido", "possivelmente").
3. Datas: `data` de um movimento é quando o ato foi praticado; `detalhes.data_agendada` é quando o evento vai acontecer. Ex.: "em 01/09/2026 foi designada audiência de conciliação para 03/03/2027 às 15:00". Nunca troque uma pela outra.
4. Itens com "suspeito": true (ver ALERTAS) são prováveis erros do cadastro do tribunal. Explique o termo de forma genérica e diga que ele consta no registro do processo, sem afirmar que há embargos/recurso em andamento.
5. Ignore campos que os ALERTAS apontam como inconsistentes.
6. Se `citacao_registrada` for false, não diga que o réu foi citado nem que apresentou defesa.
7. Não reproduza CPF, CNPJ ou OAB nos textos (o layout já exibe esses dados).
8. Não dê conselhos jurídicos nem recomendações.

## O que escrever
- `introducao`: 2 parágrafos. (1) número do processo, tribunal, comarca, fase, situação, quem processa quem, assunto e valor da causa. (2) o andamento até agora e a movimentação mais relevante, com o que ela significa.
- `partes`: para CADA parte (pelo `id`), 2 a 3 frases sobre o papel dela no processo; para autores e réus, mencione o advogado, se houver.
- `classes` e `assuntos`: para CADA item (pelo `id`), 1 a 3 frases explicando o termo, apoiadas no CONTEXTO.
- `movimentacoes`: 1 parágrafo em ordem cronológica, agrupando atos administrativos repetitivos (remessas, intimações em série) e destacando os relevantes (distribuição, audiências, prazos, decisões).
"""

TOOL = {
    "name": "registrar_resumo",
    "description": "Registra os textos do resumo do processo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "introducao": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
            "partes": {"type": "array", "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "descricao": {"type": "string"}},
                "required": ["id", "descricao"]}},
            "classes": {"type": "array", "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "explicacao": {"type": "string"}},
                "required": ["id", "explicacao"]}},
            "assuntos": {"type": "array", "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "explicacao": {"type": "string"}},
                "required": ["id", "explicacao"]}},
            "movimentacoes": {"type": "string"},
        },
        "required": ["introducao", "partes", "classes", "assuntos", "movimentacoes"],
    },
}


def _fatos_para_llm(f: Fatos) -> dict:
    d = f.to_dict()
    # o modelo não precisa dos documentos: o layout os exibe a partir do parser
    for p in d["partes"]:
        p.pop("documento", None)
    return d


def _fmt_trechos(trechos: list[Trecho]) -> str:
    return "\n".join(f"- [{t.id}] {t.termo}: {t.texto}" for t in trechos) or "- (nada encontrado)"


def montar_mensagem(fatos: Fatos, ctx: dict[str, list[Trecho]]) -> str:
    blocos = []
    for item in [*fatos.classes, *fatos.assuntos]:
        blocos.append(f"### {item.id} — {item.nome}\n{_fmt_trechos(ctx.get(item.id, []))}")
    blocos.append(f"### Movimentações\n{_fmt_trechos(ctx.get('movimentacoes', []))}")
    blocos.append(f"### Conceitos gerais\n{_fmt_trechos(ctx.get('conceitos', []))}")

    alertas = "\n".join(f"- {a}" for a in fatos.alertas) or "- nenhum"
    ids = {
        "partes": [p.id for p in fatos.partes],
        "classes": [c.id for c in fatos.classes],
        "assuntos": [a.id for a in fatos.assuntos],
    }
    return (
        "<fatos>\n" + json.dumps(_fatos_para_llm(fatos), ensure_ascii=False, indent=1) + "\n</fatos>\n\n"
        "<alertas>\n" + alertas + "\n</alertas>\n\n"
        "<contexto>\n" + "\n\n".join(blocos) + "\n</contexto>\n\n"
        f"Gere o resumo chamando `registrar_resumo`. Inclua exatamente estes ids: {json.dumps(ids)}."
    )

"""Monta o markup final no mesmo formato do summary_ia da Judit
(Markdown + tags com className para front React/Tailwind)."""
from __future__ import annotations

import html
import re

from .parser import Fatos

# tags inline que o LLM pode usar; qualquer outra é removida
TAGS_PERMITIDAS = {
    '<strong>': "strong",
    '<strong className="text-primary">': "strong",
    '<span className="font-bold">': "span",
    '<span className="text-primary">': "span",
}
RE_TAG = re.compile(r"<[^<>]*>")


def sanitizar_inline(texto: str) -> str:
    """Escapa o texto e mantém só as tags permitidas, bem aninhadas.
    Se o aninhamento estiver quebrado, devolve o texto sem nenhuma tag."""
    texto = re.sub(r"\s+", " ", texto or "").strip()
    partes, pilha, ok = [], [], True
    pos = 0
    for m in RE_TAG.finditer(texto):
        partes.append(html.escape(texto[pos:m.start()], quote=False))
        tag = m.group(0).replace("class=", "className=").replace("'", '"')
        tag = re.sub(r"\s+", " ", tag).replace(" >", ">")
        if tag in TAGS_PERMITIDAS:
            pilha.append(TAGS_PERMITIDAS[tag])
            partes.append(tag)
        elif tag in ("</strong>", "</span>"):
            nome = tag[2:-1]
            if pilha and pilha[-1] == nome:
                pilha.pop()
                partes.append(tag)
            else:
                ok = False
        # demais tags: descartadas
        pos = m.end()
    partes.append(html.escape(texto[pos:], quote=False))
    if not ok or pilha:
        return html.escape(RE_TAG.sub("", texto), quote=False)
    return "".join(partes)


def _e(s: str | None) -> str:
    return html.escape(s or "", quote=False)


def _li_parte(nome: str, doc: str | None, desc: str) -> str:
    linhas = [
        "  <li>",
        "    <p>",
        f'      <strong className="text-foreground text-base leading-5 font-semibold">{_e(nome)}</strong>',
    ]
    if doc:
        linhas.append(f'      <span className="text-muted-foreground text-sm">{_e(doc)}</span>')
    linhas += [
        "      <br />",
        '      <p className="text-foreground/70 text-md font-normal">',
        f"        {desc}",
        "      </p>",
        '      <br className="last-of-type:hidden"/>',
        "    </p>",
        "  </li>",
    ]
    return "\n".join(linhas)


def _li_item(nome: str, explicacao: str, br_final: bool) -> str:
    linhas = [
        "  <li>",
        "    <p>",
        f'      <strong className="text-foreground text-base leading-5 font-semibold">{_e(nome)}</strong>',
        "      <br />",
        '      <p className="text-foreground/70 text-md font-normal">',
        f"        {explicacao}",
        "      </p>",
    ]
    if br_final:
        linhas.append("      <br />")
    linhas += ["    </p>", "  </li>"]
    return "\n".join(linhas)


def renderizar(fatos: Fatos, secoes: dict) -> str:
    """`secoes` = saída do LLM (ou do gerador por template), já validada."""
    partes_txt = {p["id"]: sanitizar_inline(p["descricao"]) for p in secoes.get("partes", [])}
    classes_txt = {c["id"]: sanitizar_inline(c["explicacao"]) for c in secoes.get("classes", [])}
    assuntos_txt = {a["id"]: sanitizar_inline(a["explicacao"]) for a in secoes.get("assuntos", [])}

    out = ["# Resumo do processo", ""]
    for par in secoes.get("introducao", []):
        out += [sanitizar_inline(par), ""]

    if fatos.partes:
        out += ["## Partes", "", "<ul>"]
        out += [_li_parte(p.nome, p.documento, partes_txt.get(p.id, "")) for p in fatos.partes]
        out += ["</ul>", ""]

    if fatos.classes:
        out += ["## Classe", "", "<ul>"]
        out += [_li_item(c.nome, classes_txt.get(c.id, ""), br_final=False) for c in fatos.classes]
        out += ["</ul>", ""]

    if fatos.assuntos:
        out += ["## Assuntos", "", "<ul>"]
        out += [_li_item(a.nome, assuntos_txt.get(a.id, ""), br_final=True) for a in fatos.assuntos]
        out += ["</ul>", ""]

    out += ["## Movimentações", "", '<p className="text-foreground/70">',
            f"  {sanitizar_inline(secoes.get('movimentacoes', ''))}", "</p>"]
    return "\n".join(out)

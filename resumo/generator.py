"""Geradores de texto: Claude (RAG) e template determinístico (fallback / sem API)."""
from __future__ import annotations

import os
from collections import defaultdict

from .knowledge import Trecho
from .parser import Fatos, Parte
from .prompt import SYSTEM_PROMPT, TOOL, montar_mensagem

MODELO_PADRAO = os.getenv("RESUMO_MODELO", "claude-sonnet-5-5")


def _nomes(nomes: list[str]) -> str:
    nomes = [f'<span className="font-bold">{n}</span>' for n in nomes]
    if len(nomes) <= 1:
        return "".join(nomes)
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


def _lista(itens: list[str]) -> str:
    if len(itens) <= 1:
        return "".join(itens)
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def _data(d: str | None) -> str:
    return f'<span className="text-primary">{d}</span>' if d else ""


# ======================================================================
# Gerador por template (sem LLM)
# ======================================================================
class GeradorTemplate:
    nome = "template"

    def _descricao_parte(self, p: Parte, fatos: Fatos) -> str:
        tipo = p.tipo.upper()
        if "ADVOGAD" in tipo:
            clientes = [x.nome for x in fatos.partes if p.nome in x.advogados]
            alvo = f"de {_lista([f'<strong>{c}</strong>' for c in clientes])}" if clientes else "de uma das partes"
            return (f"É <strong>advogado(a)</strong> e atua na representação {alvo}. Sua função é apresentar "
                    "petições, acompanhar audiências e praticar os atos necessários para conduzir a ação.")
        adv = (f" Conta com a representação de <strong>{_lista(p.advogados)}</strong>." if p.advogados else "")
        if p.polo == "ativo":
            return ("Integra o <strong>polo ativo</strong> da ação, ou seja, é quem levou o pedido ao Judiciário. "
                    "Em termos simples, é quem afirma ter sofrido algum prejuízo e busca uma solução judicial "
                    "para os fatos narrados no processo." + adv)
        if p.polo == "passivo":
            sem_adv = "" if p.advogados else " Até o momento, não há advogado cadastrado em seu nome no processo."
            cit = "" if fatos.citacao_registrada else " Ainda não consta registro de citação."
            return ("Integra o <strong>polo passivo</strong>, isto é, é a parte apontada como responsável pelos "
                    "fatos discutidos e que poderá apresentar sua defesa e sua versão ao juiz." + adv + sem_adv + cit)
        return f"Participa do processo como <strong>{p.tipo.lower()}</strong>."

    def _explicacao(self, item, trechos: list[Trecho]) -> str:
        base = trechos[0].texto if trechos else "Termo registrado no cadastro do processo."
        if getattr(item, "suspeito", False):
            return ("Este termo consta no registro do processo, mas pode ser apenas um dado do sistema do "
                    "tribunal, já que a ação está em fase inicial. " + base)
        return base

    def _movimentacoes(self, f: Fatos) -> str:
        frases = []
        por_tipo = defaultdict(list)
        for m in f.movimentos:
            por_tipo[m.tipo].append(m)

        for m in por_tipo["distribuicao"][:1]:
            forma = " por sorteio" if m.detalhes.get("forma") == "sorteio" else ""
            frases.append(f"O processo foi <strong>distribuído{forma}</strong> em {_data(m.data)}, "
                          "o que marca o registro inicial do caso no juízo competente.")
        if por_tipo["juizo_100_digital"]:
            frases.append("Na sequência, foi <strong>incluído no Juízo 100% Digital</strong>, o que significa "
                          "que os atos processuais, inclusive audiências, tendem a ocorrer de forma eletrônica.")
        for m in por_tipo["audiencia_designada"]:
            mod = m.detalhes.get("modalidade", "")
            quando = f" para {_data(m.detalhes['data_agendada'])}" if m.detalhes.get("data_agendada") else ""
            frases.append(f"Em {_data(m.data)} foi designada <strong>audiência{' de ' + mod if mod else ''}</strong>"
                          f"{quando}.")

        grupos = defaultdict(list)
        for m in por_tipo["intimacao"] + por_tipo["citacao"]:
            grupos[(m.tipo, m.data, m.detalhes.get("prazo_dias"), m.detalhes.get("finalidade"))].append(m)
        for (tipo, data, prazo, finalidade), ms in grupos.items():
            dest = [x.detalhes["destinatario"].split(" (")[0] for x in ms if x.detalhes.get("destinatario")]
            ato = "citações" if tipo == "citacao" else "intimações eletrônicas"
            if len(ms) == 1:
                ato = "citação" if tipo == "citacao" else "intimação eletrônica"
            verbo = "foi expedida" if len(ms) == 1 else "foram expedidas"
            alvo = f" para {_nomes(dest)}" if dest else ""
            fim = f" para {finalidade}" if finalidade else ""
            pz = f", com prazo de <strong>{prazo} dia{'s' if prazo != 1 else ''}</strong>" if prazo and not finalidade else ""
            frases.append(f"Em {_data(data)} {verbo} {ato}{alvo}{fim}{pz}.")

        n = len(por_tipo["remessa_interna"])
        if n:
            frases.append(f"Houve ainda {'uma remessa interna' if n == 1 else f'{n} remessas internas'} dos autos "
                          "entre setores do juízo, movimentação de caráter administrativo.")
        if por_tipo["ato_ordinatorio"] or por_tipo["publicacao_diario"]:
            frases.append("Também foram praticados <strong>atos ordinatórios</strong>, com encaminhamento de "
                          "comunicações para publicação no diário eletrônico.")
        n = len(por_tipo["juntada"])
        if n:
            frases.append(f"Consta{'m' if n > 1 else ''} {n} juntada{'s' if n > 1 else ''} de documento nos autos.")
        for tipo in ("decisao", "despacho", "sentenca", "contestacao", "acordo", "transito_em_julgado",
                     "arquivamento", "conclusao", "outro"):
            for m in por_tipo[tipo]:
                frases.append(f"Em {_data(m.data)}: {m.texto.capitalize()}.")
        if f.movimentos:
            frases.append(f"A última movimentação registrada é de {_data(f.movimentos[-1].data)}.")
        return " ".join(frases)

    def _introducao(self, f: Fatos) -> list[str]:
        autores = [p.nome for p in f.partes if p.polo == "ativo" and "ADVOGAD" not in p.tipo]
        reus = [p.nome for p in f.partes if p.polo == "passivo" and "ADVOGAD" not in p.tipo]
        assuntos = [f"<strong>{a.nome.lower()}</strong>" for a in f.assuntos if not a.nome.upper().startswith("DIREITO ")]
        trib = f"{f.tribunal_nome} ({f.tribunal_sigla})" if f.tribunal_nome != f.tribunal_sigla else f.tribunal_sigla
        p1 = (f'O processo de número <strong className="text-primary">{f.cnj}</strong> tramita no '
              f"<strong>{trib}</strong>"
              + (f", na comarca de <strong>{f.comarca}</strong>" if f.comarca else "")
              + (f", e está em fase <strong>{f.fase}</strong>" if f.fase else "")
              + (f", com situação <strong>{f.status}</strong>" if f.status else "") + ". "
              + f"Trata-se de uma demanda proposta por {_nomes(autores)} em face de {_nomes(reus)}"
              + (f", envolvendo {_lista(assuntos)}" if assuntos else "")
              + (f", com valor atribuído de <strong>{f.valor_causa}</strong>" if f.valor_causa else "") + ".")
        aud = next((m for m in f.movimentos if m.tipo == "audiencia_designada"), None)
        p2 = ("Pelo andamento registrado, o feito foi <strong>distribuído</strong>"
              + (f" em {_data(f.data_distribuicao)}" if f.data_distribuicao else "")
              + (" e <strong>incluído no Juízo 100% Digital</strong>" if f.juizo_100_digital else "") + ".")
        if aud and aud.detalhes.get("data_agendada"):
            p2 += (f" A movimentação mais relevante até agora é a <strong>designação de audiência"
                   f"{' de ' + aud.detalhes.get('modalidade', '') if aud.detalhes.get('modalidade') else ''}</strong>, "
                   f"marcada para <strong>{_data(aud.detalhes['data_agendada'])}</strong>.")
        if not f.citacao_registrada:
            p2 += " Ainda não há registro de citação da parte ré."
        return [p1, p2]

    def gerar(self, fatos: Fatos, ctx: dict[str, list[Trecho]]) -> dict:
        return {
            "introducao": self._introducao(fatos),
            "partes": [{"id": p.id, "descricao": self._descricao_parte(p, fatos)} for p in fatos.partes],
            "classes": [{"id": c.id, "explicacao": self._explicacao(c, ctx.get(c.id, []))} for c in fatos.classes],
            "assuntos": [{"id": a.id, "explicacao": self._explicacao(a, ctx.get(a.id, []))} for a in fatos.assuntos],
            "movimentacoes": self._movimentacoes(fatos),
        }


# ======================================================================
# Gerador com Claude (RAG)
# ======================================================================
class GeradorClaude:
    def __init__(self, modelo: str = MODELO_PADRAO, max_tokens: int = 4096, client=None):
        self.modelo = modelo
        self.nome = modelo
        self.max_tokens = max_tokens
        self._client = client
        self._fallback = GeradorTemplate()

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()  # lê ANTHROPIC_API_KEY do ambiente
        return self._client

    def chamar(self, mensagem: str) -> dict:
        resp = self.client.messages.create(
            model=self.modelo,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=[TOOL],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": mensagem}],
        )
        for bloco in resp.content:
            if bloco.type == "tool_use" and bloco.name == TOOL["name"]:
                return dict(bloco.input)
        raise RuntimeError(f"Resposta sem tool_use (stop_reason={resp.stop_reason})")

    def gerar(self, fatos: Fatos, ctx: dict[str, list[Trecho]]) -> dict:
        bruto = self.chamar(montar_mensagem(fatos, ctx))
        return completar(bruto, self._fallback.gerar(fatos, ctx))


def completar(secoes: dict, padrao: dict) -> dict:
    """Garante todos os ids esperados: descarta ids desconhecidos e preenche os
    que o LLM esqueceu com o texto do template."""
    out = {
        "introducao": [p for p in secoes.get("introducao") or [] if isinstance(p, str) and p.strip()]
                      or padrao["introducao"],
        "movimentacoes": (secoes.get("movimentacoes") or "").strip() or padrao["movimentacoes"],
    }
    for chave, campo in (("partes", "descricao"), ("classes", "explicacao"), ("assuntos", "explicacao")):
        dados = {x.get("id"): x.get(campo) for x in secoes.get(chave) or [] if isinstance(x, dict)}
        out[chave] = [{"id": p["id"], campo: (dados.get(p["id"]) or "").strip() or p[campo]}
                      for p in padrao[chave]]
    return out

"""Converte a resposta da Judit (response_type == "lawsuit") em fatos normalizados.

Tudo que pode ser feito de forma determinística é feito aqui, ANTES do LLM:
- mascaramento de CPF/CNPJ (o LLM nunca vê o documento completo);
- interpretação das movimentações (data do ato x data agendada, prazos);
- detecção de inconsistências do cadastro (área errada, classe suspeita etc.).

Isso evita os erros que o summary_ia original cometeu (CPF mascarado errado,
"audiência designada em 03/03/2027", explicação de "Embargos" inexistentes).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")

TRIBUNAIS = {
    "TJAC": "Tribunal de Justiça do Acre", "TJAL": "Tribunal de Justiça de Alagoas",
    "TJAP": "Tribunal de Justiça do Amapá", "TJAM": "Tribunal de Justiça do Amazonas",
    "TJBA": "Tribunal de Justiça da Bahia", "TJCE": "Tribunal de Justiça do Ceará",
    "TJDFT": "Tribunal de Justiça do Distrito Federal e dos Territórios",
    "TJES": "Tribunal de Justiça do Espírito Santo", "TJGO": "Tribunal de Justiça de Goiás",
    "TJMA": "Tribunal de Justiça do Maranhão", "TJMT": "Tribunal de Justiça de Mato Grosso",
    "TJMS": "Tribunal de Justiça de Mato Grosso do Sul", "TJMG": "Tribunal de Justiça de Minas Gerais",
    "TJPA": "Tribunal de Justiça do Pará", "TJPB": "Tribunal de Justiça da Paraíba",
    "TJPR": "Tribunal de Justiça do Paraná", "TJPE": "Tribunal de Justiça de Pernambuco",
    "TJPI": "Tribunal de Justiça do Piauí", "TJRJ": "Tribunal de Justiça do Rio de Janeiro",
    "TJRN": "Tribunal de Justiça do Rio Grande do Norte", "TJRS": "Tribunal de Justiça do Rio Grande do Sul",
    "TJRO": "Tribunal de Justiça de Rondônia", "TJRR": "Tribunal de Justiça de Roraima",
    "TJSC": "Tribunal de Justiça de Santa Catarina", "TJSP": "Tribunal de Justiça de São Paulo",
    "TJSE": "Tribunal de Justiça de Sergipe", "TJTO": "Tribunal de Justiça do Tocantins",
}

SITUACOES = {"ATIVO": "ativa", "ARQUIVADO": "arquivada", "FINALIZADO": "finalizada",
             "SUSPENSO": "suspensa", "BAIXADO": "baixada", "EXTINTO": "extinta"}

# Classes que normalmente são incidentais/recursais: numa ação em fase inicial,
# ao lado de uma classe principal, costumam ser erro de captura do crawler.
CLASSES_INCIDENTAIS = ("EMBARGOS", "AGRAVO", "RECURSO", "APELACAO", "RECLAMACAO")


def norm(txt: str | None) -> str:
    """Maiúsculas, sem acentos, espaços colapsados."""
    if not txt:
        return ""
    t = unicodedata.normalize("NFKD", txt)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().upper()


def _digitos(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def mascarar_documento(doc: str | None) -> str | None:
    d = _digitos(doc)
    if len(d) == 11:
        return f"CPF: ***.{d[3:6]}.{d[6:9]}-**"
    if len(d) == 14:
        return f"CNPJ: **.{d[2:5]}.{d[5:8]}/{d[8:12]}-**"
    return None


def formatar_oab(documentos: list[dict]) -> str | None:
    for d in documentos or []:
        if (d.get("document_type") or "").lower() == "oab":
            m = re.match(r"([A-Za-z]{2})\s*(\d+)", d.get("document", ""))
            if m:
                return f"OAB/{m.group(1).upper()} {m.group(2)}"
    return None


def para_local(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ)


def fmt_data(dt: datetime | None) -> str | None:
    return dt.strftime("%d/%m/%Y") if dt else None


def fmt_moeda(v: float | int | None) -> str | None:
    if v is None:
        return None
    s = f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


@dataclass
class Parte:
    id: str
    nome: str
    tipo: str              # AUTOR, RÉU, ADVOGADO...
    polo: str              # ativo | passivo | outro
    documento: str | None  # já mascarado / formatado
    advogados: list[str] = field(default_factory=list)


@dataclass
class ItemCadastro:
    id: str
    codigo: str | None
    nome: str
    suspeito: bool = False
    motivo: str | None = None


@dataclass
class Movimento:
    numero: int | None
    data: str | None          # data em que o ato foi praticado (dd/mm/aaaa)
    texto: str
    tipo: str
    detalhes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fatos:
    cnj: str
    instancia: int | None
    tribunal_sigla: str | None
    tribunal_nome: str | None
    comarca: str | None
    uf: str | None
    orgao_julgador: str | None
    juiz: str | None
    fase: str | None
    status: str | None
    valor_causa: str | None
    data_distribuicao: str | None
    juizo_100_digital: bool
    citacao_registrada: bool
    segredo_justica: bool
    partes: list[Parte]
    classes: list[ItemCadastro]
    assuntos: list[ItemCadastro]
    movimentos: list[Movimento]
    alertas: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- extração

def extrair_processos(payload: dict) -> list[dict]:
    """Aceita a resposta completa (page_data), um item de page_data ou o response_data."""
    if "page_data" in payload:
        return [i["response_data"] for i in payload["page_data"]
                if i.get("response_type") == "lawsuit"]
    if payload.get("response_type") == "lawsuit":
        return [payload["response_data"]]
    if "code" in payload and "parties" in payload:
        return [payload]
    return []


def _polo(side: str | None) -> str:
    s = (side or "").lower()
    return {"active": "ativo", "passive": "passivo"}.get(s, "outro")


def _partes(raw: list[dict]) -> list[Parte]:
    partes: list[Parte] = []
    vistos: set[str] = set()
    advogados_por_polo: dict[str, list[tuple[str, str | None]]] = {}

    for p in raw or []:
        nome = (p.get("name") or "").strip()
        if not nome or norm(nome) in vistos:
            continue
        tipo = (p.get("person_type") or "").upper()
        polo = _polo(p.get("side"))
        if "ADVOGAD" in norm(tipo):
            doc = formatar_oab(p.get("documents"))
        else:
            doc = mascarar_documento(p.get("main_document"))
        advs = [a.get("name", "").strip() for a in p.get("lawyers") or [] if a.get("name")]
        for a in p.get("lawyers") or []:
            advogados_por_polo.setdefault(polo, []).append(
                (a.get("name", "").strip(), formatar_oab(a.get("documents"))))
        partes.append(Parte(id="", nome=nome, tipo=tipo, polo=polo, documento=doc, advogados=advs))
        vistos.add(norm(nome))

    # advogados que só aparecem dentro de "lawyers": insere após o último do mesmo polo
    for polo, advs in advogados_por_polo.items():
        for nome, oab in advs:
            if not nome or norm(nome) in vistos:
                continue
            idx = max((i for i, p in enumerate(partes) if p.polo == polo), default=len(partes) - 1)
            partes.insert(idx + 1, Parte(id="", nome=nome, tipo="ADVOGADO", polo=polo, documento=oab))
            vistos.add(norm(nome))

    for i, p in enumerate(partes, 1):
        p.id = f"p{i}"
    return partes


RE_DATAHORA = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?")


def _classificar_movimento(texto: str) -> tuple[str, dict]:
    t = norm(texto)
    det: dict[str, Any] = {}
    if "AUDIENCIA" in t and "DESIGNADA" in t:
        m = re.search(r"AUDI[ÊE]NCIA DE (.+?) DESIGNADA", texto, re.I)
        if m:
            det["modalidade"] = m.group(1).strip().lower()
        datas = RE_DATAHORA.findall(texto)
        if datas:
            d, h = datas[-1]
            det["data_agendada"] = d + (f" às {h}" if h else "")
        return "audiencia_designada", det
    if "INTIMACAO" in t or "CITACAO" in t:
        tipo = "citacao" if "CITACAO" in t else "intimacao"
        m = re.search(r"PRAZO:\s*(\d+)\s*DIA", t)
        if m:
            det["prazo_dias"] = int(m.group(1))
        m = re.search(r"\(\s*([A-ZÉÁÍÓÚ ]+?)\s*-\s*([^)]+)\)", texto.upper())
        if m:
            det["destinatario"] = f"{m.group(2).strip()} ({m.group(1).strip().lower()})"
        if "AUDIENCIA" in t:
            det["finalidade"] = "comunicar audiência"
        m = re.search(r"REFER\.?\s*AO EVENTO\s*(\d+)", t)
        if m:
            det["referente_evento"] = int(m.group(1))
        datas = RE_DATAHORA.findall(texto)
        if datas:
            det["disponibilizacao_diario"] = datas[-1][0]
        return tipo, det
    regras = [
        ("DISTRIBUIDO", "distribuicao"), ("100% DIGITAL", "juizo_100_digital"),
        ("REMETIDOS OS AUTOS", "remessa_interna"), ("ATO ORDINATORIO", "ato_ordinatorio"),
        ("DISPONIBILIZADO NO DJ", "publicacao_diario"), ("JUNTADA", "juntada"),
        ("SENTENCA", "sentenca"), ("DECISAO", "decisao"), ("DESPACHO", "despacho"),
        ("CONTESTACAO", "contestacao"), ("CONCLUSOS", "conclusao"), ("ARQUIVAD", "arquivamento"),
        ("TRANSITO EM JULGADO", "transito_em_julgado"), ("ACORDO", "acordo"),
    ]
    for chave, tipo in regras:
        if chave in t:
            if tipo == "distribuicao" and "SORTEIO" in t:
                det["forma"] = "sorteio"
            return tipo, det
    return "outro", det


def _movimentos(steps: list[dict]) -> list[Movimento]:
    movs = []
    for s in steps or []:
        if s.get("private"):
            continue
        conteudo = re.sub(r"\s+", " ", s.get("content") or "").strip()
        m = re.match(r"^(\d+)\s*-\s*(.*)$", conteudo)
        numero, texto = (int(m.group(1)), m.group(2)) if m else (None, conteudo)
        tipo, det = _classificar_movimento(texto)
        movs.append(Movimento(numero=numero, data=fmt_data(para_local(s.get("step_date"))),
                              texto=texto, tipo=tipo, detalhes=det))
    # ordem cronológica crescente (a Judit entrega do mais recente para o mais antigo)
    movs.sort(key=lambda x: (x.numero is None, x.numero or 0))
    return movs


def _itens(raw: list[dict], prefixo: str) -> list[ItemCadastro]:
    return [ItemCadastro(id=f"{prefixo}{i}", codigo=str(r.get("code")) if r.get("code") else None,
                         nome=(r.get("name") or "").strip())
            for i, r in enumerate(raw or [], 1) if r.get("name")]


def _alertas(d: dict, classes: list[ItemCadastro], assuntos: list[ItemCadastro],
             movs: list[Movimento]) -> list[str]:
    alertas = []
    fase = norm(d.get("phase"))
    tem_decisao = any(m.tipo in ("sentenca", "decisao") for m in movs)

    if len(classes) > 1:
        for c in classes:
            if any(k in norm(c.nome) for k in CLASSES_INCIDENTAIS) and fase in ("INICIAL", "") and not tem_decisao:
                c.suspeito = True
                c.motivo = ("classe incidental/recursal numa ação em fase inicial, sem decisão "
                            "registrada; provável erro de captura do cadastro")
                alertas.append(f"Classe '{c.nome}' provavelmente é erro de cadastro: {c.motivo}.")

    area = norm(d.get("area"))
    textos_assuntos = " ".join(norm(a.nome) for a in assuntos)
    if "TRABALHO" in area and "TRABALH" not in textos_assuntos and str(d.get("justice")) != "5":
        alertas.append(f"Área cadastrada '{d.get('area')}' não condiz com os assuntos "
                       f"({', '.join(a.nome for a in assuntos)}) nem com a Justiça "
                       f"({d.get('justice_description')}). Ignore a área.")

    county = norm(d.get("county"))
    orgao = norm((d.get("courts") or [{}])[0].get("name"))
    if county and orgao and "FAZENDA" in county and "JUIZADO ESPECIAL CIVEL" in orgao:
        alertas.append("O campo de comarca/vara menciona 'Vara da Fazenda Pública', mas o órgão "
                       "julgador é um Juizado Especial Cível. Use o órgão julgador.")
    return alertas


def extrair_fatos(d: dict) -> Fatos:
    partes = _partes(d.get("parties"))
    classes = _itens(d.get("classifications"), "c")
    assuntos = _itens(d.get("subjects"), "a")
    movs = _movimentos(d.get("steps"))
    alertas = _alertas(d, classes, assuntos, movs)
    sigla = d.get("tribunal_acronym")
    cidade = d.get("city")
    return Fatos(
        cnj=d.get("code"),
        instancia=d.get("instance"),
        tribunal_sigla=sigla,
        tribunal_nome=TRIBUNAIS.get(sigla or "", sigla),
        comarca=cidade.title() if cidade else None,
        uf=d.get("state"),
        orgao_julgador=((d.get("courts") or [{}])[0].get("name")),
        juiz=d.get("judge"),
        fase=(d.get("phase") or "").lower() or None,
        status=SITUACOES.get(norm(d.get("status")), (d.get("status") or "").lower() or None),
        valor_causa=fmt_moeda(d.get("amount")),
        data_distribuicao=fmt_data(para_local(d.get("distribution_date"))),
        juizo_100_digital=any(m.tipo == "juizo_100_digital" for m in movs),
        citacao_registrada=any(m.tipo == "citacao" for m in movs),
        segredo_justica=bool(d.get("secrecy_level")),
        partes=partes, classes=classes, assuntos=assuntos, movimentos=movs, alertas=alertas,
    )

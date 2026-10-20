import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from resumo.generator import GeradorClaude, GeradorTemplate
from resumo.knowledge import BaseConhecimento
from resumo.parser import extrair_fatos, extrair_processos, mascarar_documento
from resumo.pipeline import como_item_judit, gerar_resumos
from resumo.prompt import montar_mensagem
from resumo.render import sanitizar_inline

RAIZ = Path(__file__).resolve().parent.parent
PAYLOAD = json.loads((RAIZ / "tests/fixtures/judit_exemplo.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def kb():
    return BaseConhecimento.construir(RAIZ / "kb")


@pytest.fixture(scope="module")
def fatos():
    return extrair_fatos(extrair_processos(PAYLOAD)[0])


# ---------------------------------------------------------------- parser
def test_mascaramento():
    assert mascarar_documento("02727135971") == "CPF: ***.271.359-**"
    # o summary_ia original errou este (***.397.191-**)
    assert mascarar_documento("00439671914") == "CPF: ***.396.719-**"
    assert mascarar_documento("10172255000195") == "CNPJ: **.172.255/0001-**"
    assert mascarar_documento(None) is None


def test_fatos_basicos(fatos):
    assert fatos.cnj == "6000999-00.2026.8.16.0021"
    assert fatos.tribunal_nome == "Tribunal de Justiça do Paraná"
    assert fatos.valor_causa == "R$ 30.000,00"
    assert fatos.status == "ativa"
    assert fatos.juizo_100_digital and not fatos.citacao_registrada
    assert [p.tipo for p in fatos.partes] == ["AUTOR", "AUTOR", "ADVOGADO", "RÉU"]
    assert fatos.partes[2].documento == "OAB/PR 0000001"


def test_movimentos_cronologicos_e_datas(fatos):
    assert [m.numero for m in fatos.movimentos] == list(range(1, 14))
    aud = next(m for m in fatos.movimentos if m.tipo == "audiencia_designada")
    assert aud.data == "01/09/2026"
    assert aud.detalhes["data_agendada"] == "03/03/2027 às 15:00"
    assert aud.detalhes["modalidade"] == "conciliação"
    prazos = [m.detalhes.get("prazo_dias") for m in fatos.movimentos if m.tipo == "intimacao"]
    assert prazos == [1, 1, 15, 15]


def test_alertas(fatos):
    assert next(c for c in fatos.classes if c.nome == "EMBARGOS").suspeito
    assert any("DIREITO DO TRABALHO" in a for a in fatos.alertas)


# ---------------------------------------------------------------- RAG
def test_recuperacao(kb, fatos):
    ctx = kb.contexto_para(fatos)
    assert ctx["c1"][0].id == "classe-436" and ctx["c1"][0].origem == "codigo"
    assert ctx["a1"][0].id == "assunto-14033"
    ids_mov = {t.id for t in ctx["movimentacoes"]}
    assert {"mov-audiencia-conciliacao", "mov-intimacao", "mov-100-digital", "mov-djen"} <= ids_mov


def test_persistencia(tmp_path, kb):
    kb.salvar(tmp_path / "kb")
    kb2 = BaseConhecimento.carregar(tmp_path / "kb")
    assert kb2.buscar("audiência de conciliação")[0].id == kb.buscar("audiência de conciliação")[0].id


def test_prompt_sem_documentos(kb, fatos):
    msg = montar_mensagem(fatos, kb.contexto_para(fatos))
    assert "144.477" not in msg and "11144477735" not in msg
    assert "data_agendada" in msg and "<alertas>" in msg


# ---------------------------------------------------------------- render
def test_sanitizar():
    assert sanitizar_inline('a <strong>b</strong> <script>x</script>') == "a <strong>b</strong> x"
    assert sanitizar_inline('<span class="font-bold">X</span>') == '<span className="font-bold">X</span>'
    assert sanitizar_inline("<strong>quebrado</span>") == "quebrado"
    assert sanitizar_inline("R$ 1 < 2") == "R$ 1 &lt; 2"


def test_template_mesmo_formato_do_original(kb):
    [res] = gerar_resumos(PAYLOAD, kb, GeradorTemplate())
    original = PAYLOAD["page_data"][1]["response_data"]["data"][0]
    titulos = lambda s: re.findall(r"^#+ .*$", s, re.M)
    assert titulos(res.summary_ia) == titulos(original)
    # mesmos nomes/documentos na seção de partes
    spans = lambda s: re.findall(r'text-muted-foreground text-sm">([^<]+)<', s)
    assert spans(res.summary_ia)[0] == spans(original)[0]
    assert "03/03/2027 às 15:00" in res.summary_ia
    assert not re.search(r"designada[^.]*em <span[^>]*>03/03/2027", res.summary_ia)
    item = como_item_judit(res)
    assert item["response_data"]["origin_id"] == PAYLOAD["page_data"][0]["response_id"]


# ---------------------------------------------------------------- Claude (mock)
class FakeClient:
    def __init__(self, entrada):
        self.entrada, self.chamadas = entrada, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.chamadas.append(kw)
        bloco = SimpleNamespace(type="tool_use", name="registrar_resumo", input=self.entrada)
        return SimpleNamespace(content=[bloco], stop_reason="tool_use")


def test_claude_mock_completa_ids_faltantes(kb):
    entrada = {"introducao": ["Intro <b>x</b>."], "partes": [{"id": "p1", "descricao": "Autora."}],
               "classes": [], "assuntos": [{"id": "zzz", "explicacao": "lixo"}], "movimentacoes": "Movs."}
    cli = FakeClient(entrada)
    [res] = gerar_resumos(PAYLOAD, kb, GeradorClaude(modelo="teste", client=cli))
    kw = cli.chamadas[0]
    assert kw["tool_choice"] == {"type": "auto"}
    assert "Intro x." in res.summary_ia               # tag <b> removida
    assert "Autora." in res.summary_ia                # texto do LLM
    assert "polo passivo" in res.summary_ia           # p4 completado pelo template
    assert "lixo" not in res.summary_ia               # id desconhecido descartado
    assert res.gerador == "teste" and not res.usou_fallback


def test_fallback_quando_api_falha(kb):
    class Quebrado(GeradorClaude):
        def chamar(self, mensagem):
            raise RuntimeError("API fora")
    [res] = gerar_resumos(PAYLOAD, kb, Quebrado(modelo="x", client=object()))
    assert res.usou_fallback and res.gerador == "template"

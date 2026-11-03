import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from resumo.generator import GeradorTemplate
from resumo.knowledge import BaseConhecimento
from resumo.parser import extrair_processos
from resumo.pipeline import gerar_resumos

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("consultar_gerar", ROOT / "scripts" / "consultar_gerar.py")
E2E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E2E)


def test_relatorio_offline_tem_metadados_e_nao_finge_claude():
    payload = json.loads((ROOT / "tests/fixtures/judit_exemplo.json").read_text(encoding="utf-8"))
    kb = BaseConhecimento.construir(ROOT / "kb")
    resultados = gerar_resumos(payload, kb, GeradorTemplate())
    assert E2E._validar(payload, resultados) == []
    assert resultados[0].gerador == "template"
    assert not resultados[0].usou_fallback
    assert resultados[0].fontes
    assert resultados[0].cnj in resultados[0].summary_ia


def test_classificacao_de_falhas_anthropic_sem_mensagem_privada():
    assert E2E._categoria_claude(SimpleNamespace(status_code=401)) == "BLOCKED_AUTH"
    assert E2E._categoria_claude(SimpleNamespace(status_code=403)) == "BLOCKED_AUTHORIZATION"
    assert E2E._categoria_claude(SimpleNamespace(status_code=404)) == "BLOCKED_MODEL"
    assert E2E._categoria_claude(SimpleNamespace(status_code=429)) == "BLOCKED_LIMIT"

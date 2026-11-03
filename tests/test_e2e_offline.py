import hashlib
import importlib.util
import os
import json
import re
from pathlib import Path
from types import SimpleNamespace

import anthropic
from dotenv import load_dotenv

from resumo.generator import GeradorClaude, GeradorTemplate
from resumo.knowledge import BaseConhecimento
from resumo.parser import extrair_fatos, extrair_processos
from resumo.pipeline import gerar_resumos
from resumo.prompt import montar_mensagem

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/judit_exemplo.json"
SPEC = importlib.util.spec_from_file_location("consultar_gerar_e2e", ROOT / "scripts" / "consultar_gerar.py")
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


class MockClaudeClient:
    def __init__(self, response):
        self.response = response
        self.messages = SimpleNamespace(create=self.create)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="registrar_resumo", input=self.response)],
                               stop_reason="tool_use")


def payload_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def raw_documents(value):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"main_document", "document"} and isinstance(item, str):
                digits = re.sub(r"\D", "", item)
                if len(digits) in (11, 14):
                    found.append(digits)
            found.extend(raw_documents(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(raw_documents(item))
    return set(found)


def test_pipeline_real_com_mock_externo_garante_invariantes():
    payload = payload_fixture()
    data = extrair_processos(payload)[0]
    facts = extrair_fatos(data)
    kb = BaseConhecimento.construir(ROOT / "kb")
    context = kb.contexto_para(facts)
    ids_index = {entry["id"] for entry in kb.entradas}
    prompt = montar_mensagem(facts, context)

    # O prompt contém FATOS mascarados, nunca documentos pessoais completos ou OAB.
    for document in raw_documents(data):
        assert document not in prompt
    assert all(t.id in ids_index for fragments in context.values() for t in fragments)
    for movement in facts.movimentos:
        if movement.data:
            assert movement.data in prompt
        scheduled = movement.detalhes.get("data_agendada")
        if scheduled:
            assert scheduled in prompt
            assert movement.data != scheduled

    response = {
        "introducao": [f"Processo {facts.cnj} com partes {', '.join(p.nome for p in facts.partes)}.",
                        "Andamento conforme os fatos cadastrados."],
        "partes": [{"id": p.id, "descricao": f"Parte {p.nome}."} for p in facts.partes],
        "classes": [{"id": c.id, "explicacao": "Classe conforme contexto recuperado."} for c in facts.classes],
        "assuntos": [{"id": a.id, "explicacao": "Assunto conforme contexto recuperado."} for a in facts.assuntos],
        "movimentacoes": " ".join(f"Em {m.data}: {m.texto}" for m in facts.movimentos if m.data),
    }
    fake = MockClaudeClient(response)
    results = gerar_resumos(payload, kb, GeradorClaude(modelo="mock-claude", client=fake))
    assert len(results) == 1
    result = results[0]
    assert result.gerador == "mock-claude" and not result.usou_fallback
    assert set(result.fontes).issubset(ids_index)
    assert CLI._validar(payload, results, kb) == []
    assert result.cnj in result.summary_ia
    assert all(part.nome in result.summary_ia for part in facts.partes)
    movement_section = result.summary_ia.split("## Movimentações", 1)[1]
    cursor = 0
    for movement in facts.movimentos:
        if movement.data:
            position = movement_section.find(movement.data, cursor)
            assert position >= 0
            cursor = position + len(movement.data)
    for document in raw_documents(data):
        assert document not in result.summary_ia

    summary_hash = hashlib.sha256(result.summary_ia.encode("utf-8")).hexdigest()
    manifest = CLI._manifesto(payload, "fixture", "fixture-hash", "a" * 64,
                              "mock-claude", results, [], kb, summary_hash, "OFFLINE_VALIDATED")
    assert manifest["summary_sha256"] == summary_hash
    assert manifest["rag"]["source_ids_retrieved"] == result.fontes
    assert manifest["rag"]["claim_attribution"] == "NOT_TRACKED"
    assert manifest["rag"]["factual_support_validation"] == "NOT_PERFORMED"
    assert manifest["gates"]["generator"] == "PASS"


def test_fallback_reprova_gate_em_nao_publica_summary(tmp_path, monkeypatch):
    payload = payload_fixture()
    kb = BaseConhecimento.construir(ROOT / "kb")
    prefix = tmp_path / "kb"
    kb.salvar(prefix)
    # Resultado substituto marcado como fallback: gate precisa barrar publicação.
    [result] = gerar_resumos(payload, kb, GeradorTemplate())
    result.usou_fallback = True
    result.gerador = "template"
    saida, manifesto = tmp_path / "saida.md", tmp_path / "manifest.json"
    monkeypatch.setattr("sys.argv", ["consultar_gerar.py", "--entrada", str(FIXTURE), "--sem-llm",
                                     "--indice", str(prefix), "--saida", str(saida), "--relatorio", str(manifesto)])
    monkeypatch.setattr(CLI, "gerar_resumos", lambda *args, **kwargs: [result])
    assert CLI.main() == 1
    assert not saida.exists()
    report = json.loads(manifesto.read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["fallback_used"] is True


def test_gerador_carrega_dotenv_sem_sobrescrever_ambiente(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_DOTENV_MARKER=local-value\n", encoding="utf-8")
    monkeypatch.delenv("TEST_DOTENV_MARKER", raising=False)
    assert load_dotenv(dotenv_path=env_file, override=False)
    assert os.getenv("TEST_DOTENV_MARKER") == "local-value"
    monkeypatch.setenv("TEST_DOTENV_MARKER", "exported-value")
    load_dotenv(dotenv_path=env_file, override=False)
    assert os.getenv("TEST_DOTENV_MARKER") == "exported-value"

    loaded = []
    def fake_load_dotenv(override=False):
        loaded.append(override)
        os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setattr("dotenv.load_dotenv", fake_load_dotenv)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    constructed = []
    fake_sdk_client = object()
    monkeypatch.setattr(anthropic, "Anthropic", lambda: (constructed.append(bool(os.getenv("ANTHROPIC_API_KEY"))) or fake_sdk_client))
    assert GeradorClaude().client is fake_sdk_client
    assert loaded == [False] and constructed == [True]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "exported-test-key")
    assert GeradorClaude().client is fake_sdk_client
    assert loaded == [False, False]


def test_manifest_offline_hashes_and_labels_template(tmp_path, monkeypatch):
    payload = payload_fixture()
    kb = BaseConhecimento.construir(ROOT / "kb")
    prefix = tmp_path / "kb"
    kb.salvar(prefix)
    entrada = tmp_path / "fixture.json"
    entrada.write_bytes(FIXTURE.read_bytes())
    saida, manifesto = tmp_path / "summary.md", tmp_path / "manifest.json"
    monkeypatch.setattr("sys.argv", ["consultar_gerar.py", "--entrada", str(entrada), "--sem-llm",
                                     "--indice", str(prefix), "--saida", str(saida), "--relatorio", str(manifesto)])
    assert CLI.main() == 0
    report = json.loads(manifesto.read_text(encoding="utf-8"))
    summary = saida.read_bytes()
    assert report["status"] == "OFFLINE_ONLY"
    assert report["input"]["sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert report["summary_sha256"] == hashlib.sha256(summary).hexdigest()
    assert report["generator_effective"] == "template"
    assert report["model"]["effective"] is None
    assert report["rag"]["source_ids_exist_in_index"] is True
    assert report["gates"]["factual"] == "NOT_PERFORMED"
    assert report["process_ids_sha256"] and payload["page_data"][0]["response_data"]["code"] not in manifesto.read_text(encoding="utf-8")

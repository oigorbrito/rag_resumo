"""CLI: --verificacao-saida grava a verificação item a item ligada ao resumo; sem a opção nada muda (offline)."""
import hashlib
import importlib.util
import json
from pathlib import Path

from resumo.knowledge import BaseConhecimento

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/judit_exemplo.json"
_SPEC = importlib.util.spec_from_file_location("consultar_gerar_verif", ROOT / "scripts" / "consultar_gerar.py")
CLI = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CLI)


def _preparar(tmp_path):
    kb = BaseConhecimento.construir(ROOT / "kb")
    prefix = tmp_path / "kb"
    kb.salvar(prefix)
    entrada = tmp_path / "fixture.json"
    entrada.write_bytes(FIXTURE.read_bytes())
    return prefix, entrada, tmp_path / "resumo.md", tmp_path / "relatorio.json", tmp_path / "verificacao.json"


def test_verificacao_saida_grava_itens_ligados_ao_resumo(tmp_path, monkeypatch):
    prefix, entrada, saida, rel, ver = _preparar(tmp_path)
    monkeypatch.setattr("sys.argv", ["consultar_gerar.py", "--entrada", str(entrada), "--sem-llm",
                                     "--indice", str(prefix), "--saida", str(saida), "--relatorio", str(rel),
                                     "--verificacao-saida", str(ver)])
    assert CLI.main() == 0
    dados = json.loads(ver.read_text(encoding="utf-8"))
    assert dados["resumo_sha256"] == hashlib.sha256(saida.read_bytes()).hexdigest()
    assert dados["resumo_sha256"] == json.loads(rel.read_text(encoding="utf-8"))["summary_sha256"]
    assert dados["itens"]
    assert all(set(i) == {"tipo", "texto", "status", "evidencia"} for i in dados["itens"])
    assert all(i["status"] in {"confirmado", "derivado"} for i in dados["itens"])


def test_sem_a_opcao_nenhum_arquivo_de_verificacao_e_criado(tmp_path, monkeypatch):
    prefix, entrada, saida, rel, ver = _preparar(tmp_path)
    monkeypatch.setattr("sys.argv", ["consultar_gerar.py", "--entrada", str(entrada), "--sem-llm",
                                     "--indice", str(prefix), "--saida", str(saida), "--relatorio", str(rel)])
    assert CLI.main() == 0
    assert not ver.exists()

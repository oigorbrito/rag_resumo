"""Gate do CLI: não mascarar falha externa nem publicar saída inválida."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gerar_cli_gate", ROOT / "scripts" / "gerar.py")
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


def run_cli(monkeypatch, tmp_path, flags, backend):
    entrada = tmp_path / "input.json"
    entrada.write_text((ROOT / "tests/fixtures/judit_exemplo.json").read_text(encoding="utf-8"), encoding="utf-8")
    saida = tmp_path / "saida.json"
    monkeypatch.setattr(CLI, "gerar_resumos", backend)
    monkeypatch.setattr(sys, "argv", ["gerar.py", str(entrada), "--formato", "json", "--saida", str(saida), *flags])
    return saida


def test_exigir_claude_falha_sem_publicar(monkeypatch, tmp_path, caplog):
    def falha(*args, **kwargs):
        assert kwargs["fallback_template"] is False
        err = RuntimeError("conteudo privado não pode aparecer no log")
        raise err
    saida = run_cli(monkeypatch, tmp_path, ["--exigir-claude"], falha)
    with pytest.raises(SystemExit) as exc:
        CLI.main()
    assert exc.value.code == 1
    assert not saida.exists()
    assert "conteudo privado" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_modo_original_mantem_fallback_padrao(monkeypatch, tmp_path):
    def entrega(*args, **kwargs):
        assert kwargs["fallback_template"] is True
        return [SimpleNamespace(cnj="teste", summary_ia="texto", gerador="template", usou_fallback=True, alertas=[], fontes=[])]
    saida = run_cli(monkeypatch, tmp_path, [], entrega)
    CLI.main()
    doc = json.loads(saida.read_text(encoding="utf-8"))
    assert doc[0]["gerador"] == "template"


def test_sem_resultados_nao_publica(monkeypatch, tmp_path):
    saida = run_cli(monkeypatch, tmp_path, ["--exigir-claude"], lambda *a, **k: [])
    with pytest.raises(SystemExit):
        CLI.main()
    assert not saida.exists()


def test_flags_incompativeis(monkeypatch, tmp_path):
    saida = run_cli(monkeypatch, tmp_path, ["--exigir-claude", "--sem-llm"], lambda *a, **k: [])
    with pytest.raises(SystemExit) as exc:
        CLI.main()
    assert exc.value.code == 2
    assert not saida.exists()

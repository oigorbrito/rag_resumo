"""Proteção de custo: o request_id da Judit é guardado e a nova tentativa não cria outra consulta paga (offline)."""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

from resumo.judit import ClienteJudit, JuditError

ROOT = Path(__file__).resolve().parents[1]
RID = "11111111-2222-3333-4444-555555555555"
CNJ = "5000395-40.2023.8.21.0033"


def _carregar(nome, arquivo):
    spec = importlib.util.spec_from_file_location(nome, ROOT / "scripts" / arquivo)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLI = _carregar("consultar_gerar_custo", "consultar_gerar.py")
APP = _carregar("app_web_custo", "app_web.py")


class JuditFalsa:
    """Conta as chamadas; criar_consulta é a única cobrada."""
    posts = 0
    recuperacoes = 0
    aguardar_erro = None

    def __init__(self, *a, **k):
        pass

    def criar_consulta(self, cnj):
        JuditFalsa.posts += 1
        return RID

    def aguardar(self, request_id, **k):
        if JuditFalsa.aguardar_erro:
            raise JuditFalsa.aguardar_erro

    def recuperar(self, request_id):
        JuditFalsa.recuperacoes += 1
        raise JuditError("Consulta Judit ainda não está concluída", category="pending")

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _zerar():
    JuditFalsa.posts = JuditFalsa.recuperacoes = 0
    JuditFalsa.aguardar_erro = None


def _rodar_cli(monkeypatch, tmp_path, *args):
    monkeypatch.setattr(CLI, "ClienteJudit", JuditFalsa)
    monkeypatch.setattr("sys.argv", ["consultar_gerar.py", *args,
                                     "--saida", str(tmp_path / "r.md"), "--relatorio", str(tmp_path / "r.json")])
    return CLI.main()


def test_timeout_da_judit_guarda_o_request_id(tmp_path, monkeypatch):
    JuditFalsa.aguardar_erro = JuditError("Tempo limite", category="timeout")
    arq = tmp_path / "request_id.txt"
    assert _rodar_cli(monkeypatch, tmp_path, "--cnj", CNJ, "--request-id-saida", str(arq)) == 1
    assert arq.read_text(encoding="utf-8") == RID
    rel = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert rel["status"] == "BLOCKED_JUDIT_TIMEOUT"
    assert JuditFalsa.posts == 1


def test_retomada_por_request_id_nao_faz_nova_consulta_paga(tmp_path, monkeypatch):
    _rodar_cli(monkeypatch, tmp_path, "--request-id", RID)
    assert JuditFalsa.posts == 0
    assert JuditFalsa.recuperacoes == 1


def test_aguardar_nao_repete_o_post():
    chamadas = []

    class Cli:
        def post(self, path, json=None):
            chamadas.append(("POST", path))
            raise AssertionError("aguardar/recuperar não podem fazer POST")

        def get(self, path, **k):
            chamadas.append(("GET", path))

            class R:
                status_code = 200
                def json(self_inner):
                    return {"request_id": RID, "status": "pending"}
            return R()

    c = ClienteJudit(api_key="x", cliente=Cli())
    with pytest.raises(JuditError):
        c.aguardar(RID, timeout=0.01, intervalo=0)
    assert all(m == "GET" for m, _ in chamadas)


def test_request_id_reaproveitavel():
    f = APP.request_id_reaproveitavel
    falhou = {"cnj": CNJ, "status": "FALHOU", "request_id": RID, "motivo": "BLOCKED_JUDIT_TIMEOUT: x"}
    assert f([falhou], CNJ) == RID
    assert f([falhou], "9999999-99.2023.8.21.0033") == ""
    assert f([{"cnj": CNJ, "status": "PASS"}, falhou], CNJ) == ""  # já existe resumo mais novo
    assert f([{**falhou, "motivo": "BLOCKED_JUDIT_CANCELLED: x"}], CNJ) == ""
    assert f([{**falhou, "request_id": ""}], CNJ) == ""


def test_app_envia_cnj_com_arquivo_de_id_e_retoma_com_request_id(monkeypatch):
    comandos = []

    class P:
        def poll(self): return 0

    monkeypatch.setattr(APP.subprocess, "Popen", lambda cmd, **k: comandos.append(cmd) or P())
    anterior = os.environ.get("RESUMO_DADOS_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["RESUMO_DADOS_DIR"] = tmp
        try:
            APP.iniciar_geracao(CNJ, "u")
            meta = APP.iniciar_geracao(CNJ, "u", RID)
        finally:
            if anterior is None:
                os.environ.pop("RESUMO_DADOS_DIR", None)
            else:
                os.environ["RESUMO_DADOS_DIR"] = anterior
    nova, retomada = comandos
    assert "--cnj" in nova and "--request-id-saida" in nova and "--request-id" not in nova
    assert "--request-id" in retomada and RID in retomada and "--cnj" not in retomada
    assert meta["request_id"] == RID


def test_atualizar_status_le_o_request_id_gravado_pelo_cli():
    anterior = os.environ.get("RESUMO_DADOS_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["RESUMO_DADOS_DIR"] = tmp
        try:
            pasta = Path(tmp) / "abc"
            pasta.mkdir()
            (pasta / "request_id.txt").write_text(RID, encoding="utf-8")
            meta = {"id": "abc", "cnj": CNJ, "status": "FALHOU"}
            (pasta / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            assert APP.atualizar_status(meta)["request_id"] == RID
            assert json.loads((pasta / "meta.json").read_text(encoding="utf-8"))["request_id"] == RID
        finally:
            if anterior is None:
                os.environ.pop("RESUMO_DADOS_DIR", None)
            else:
                os.environ["RESUMO_DADOS_DIR"] = anterior


def test_cliente_claude_tem_limites_explicitos():
    texto = (ROOT / "resumo" / "generator.py").read_text(encoding="utf-8")
    assert "max_retries=1" in texto and "timeout=" in texto

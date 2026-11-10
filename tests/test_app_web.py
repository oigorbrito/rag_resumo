"""Interface web do piloto: acesso, verificação, revisão humana, auditoria e proteção de origem (offline)."""
import base64
import contextlib
import hashlib
import http.client
import importlib.util
import json
import os
import tempfile
import threading
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("app_web_teste", ROOT / "scripts" / "app_web.py")
APP = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(APP)

AUTH = {"Authorization": "Basic " + base64.b64encode(b"maria:segredo").decode()}


@contextlib.contextmanager
def servidor():
    anteriores = {k: os.environ.get(k) for k in ("APP_SENHA", "RESUMO_DADOS_DIR")}
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["APP_SENHA"] = "segredo"
        os.environ["RESUMO_DADOS_DIR"] = tmp
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), APP.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1], Path(tmp)
        finally:
            httpd.shutdown()
            httpd.server_close()
            for k, v in anteriores.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _req(port, metodo, caminho, headers=None, corpo=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = dict(headers or {})
    if corpo is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(metodo, caminho, body=corpo, headers=h)
    r = conn.getresponse()
    dados = r.read().decode("utf-8", "replace")
    conn.close()
    return r.status, dados


def _criar_job(dados: Path, status="PASS") -> str:
    job = uuid.uuid4().hex
    pasta = dados / job
    pasta.mkdir(parents=True)
    texto = ('# Resumo do processo\n\nDistribuído em <strong className="x">09/01/2023</strong>, '
             'valor da causa R$ 28.734,71.\n')
    (pasta / "resumo.md").write_text(texto, encoding="utf-8", newline="\n")
    sha = hashlib.sha256((pasta / "resumo.md").read_bytes()).hexdigest()
    (pasta / "relatorio.json").write_text(json.dumps(
        {"status": "PASS", "generator_effective": "claude-teste", "rag": {"source_ids_retrieved": ["a", "b"]}}))
    itens = [
        {"tipo": "data", "texto": "09/01/2023", "status": "confirmado",
         "evidencia": "Movimento: DISTRIBUÍDO POR SORTEIO"},
        {"tipo": "valor", "texto": "R$ 28.734,71", "status": "confirmado",
         "evidencia": "Valor da causa informado no processo"},
    ]
    (pasta / "verificacao.json").write_text(json.dumps({"itens": itens, "resumo_sha256": sha}))
    (pasta / "meta.json").write_text(json.dumps(
        {"id": job, "cnj": "5000395-40.2023.8.21.0033", "criado_em": "2026-11-10 10:00:00",
         "concluido_em": "2026-11-10 10:05:00", "status": status}))
    return job


def _origem(port):
    return {**AUTH, "Origin": f"http://127.0.0.1:{port}"}


def test_sem_senha_ou_com_senha_errada_e_401():
    with servidor() as (port, _):
        assert _req(port, "GET", "/")[0] == 401
        errada = {"Authorization": "Basic " + base64.b64encode(b"maria:errada").decode()}
        assert _req(port, "GET", "/", errada)[0] == 401


def test_cnj_invalido_e_rejeitado_sem_iniciar_geracao():
    with servidor() as (port, dados):
        status, _ = _req(port, "POST", "/gerar", AUTH, "cnj=123")
        assert status == 400
        assert not [p for p in dados.iterdir() if p.is_dir()]


def test_pagina_do_resumo_mostra_verificacao_destaques_e_pede_revisao():
    with servidor() as (port, dados):
        job = _criar_job(dados)
        status, corpo = _req(port, "GET", f"/job/{job}", AUTH)
        assert status == 200
        assert 'class="v-ok"' in corpo or '<mark class="v-ok"' in corpo
        assert "Aguardando revisão humana" in corpo
        assert "todos encontrados nos dados do processo" in corpo
        assert "Movimento: DISTRIBUÍDO POR SORTEIO" in corpo
        assert "className" not in corpo and "<strong " not in corpo.split("<pre")[1]


def test_revisao_registra_quem_quando_e_auditoria_sem_duplicar():
    with servidor() as (port, dados):
        job = _criar_job(dados)
        corpo_form = f"job={job}&nome=Maria+Souza&obs=conferi+datas"
        status, _ = _req(port, "POST", "/revisar", _origem(port), corpo_form)
        assert status == 303
        _req(port, "POST", "/revisar", _origem(port), corpo_form)  # repetido: não duplica
        meta = json.loads((dados / job / "meta.json").read_text())
        assert len(meta["revisoes"]) == 1
        assert meta["revisoes"][0]["quem"] == "Maria Souza" and meta["revisoes"][0]["obs"] == "conferi datas"
        _, pagina = _req(port, "GET", f"/job/{job}", AUTH)
        assert "Revisado" in pagina and "Maria Souza" in pagina
        auditoria = (dados / "auditoria.log").read_text(encoding="utf-8")
        assert '"acao": "revisar"' in auditoria and '"revisor": "Maria Souza"' in auditoria


def test_revisao_vale_so_para_a_versao_revisada():
    with servidor() as (port, dados):
        job = _criar_job(dados)
        _req(port, "POST", "/revisar", _origem(port), f"job={job}&nome=Maria")
        (dados / job / "resumo.md").write_text("# Texto alterado depois da revisão\n", encoding="utf-8")
        _, pagina = _req(port, "GET", f"/job/{job}", AUTH)
        assert "Aguardando revisão humana" in pagina


def test_formulario_de_outro_site_e_bloqueado():
    with servidor() as (port, dados):
        job = _criar_job(dados)
        externo = {**AUTH, "Origin": "http://site-malicioso.example"}
        assert _req(port, "POST", "/revisar", externo, f"job={job}&nome=Maria")[0] == 403
        assert _req(port, "POST", "/gerar", externo, "cnj=5000395-40.2023.8.21.0033")[0] == 403
        meta = json.loads((dados / job / "meta.json").read_text())
        assert not meta.get("revisoes")
        assert [p.name for p in dados.iterdir() if p.is_dir()] == [job]


def test_legivel_remove_marcacoes_e_destaca_itens():
    itens = [{"texto": "09/01/2023", "status": "derivado", "evidencia": 'calculada "x"'}]
    saida = APP.legivel('<p className="a">Em <strong>09/01/2023</strong> houve algo.</p>', itens)
    assert "<p" not in saida and "className" not in saida
    assert '<mark class="v-der"' in saida and "09/01/2023</mark>" in saida
    assert "&quot;" in saida  # evidência escapada dentro do atributo


def test_revisao_exige_o_nome_de_quem_revisa():
    with servidor() as (port, dados):
        job = _criar_job(dados)
        status, corpo = _req(port, "POST", "/revisar", _origem(port), f"job={job}&nome=+++&obs=ok")
        assert status == 400 and "Informe o seu nome" in corpo
        assert not json.loads((dados / job / "meta.json").read_text()).get("revisoes")


def test_formulario_de_revisao_pede_o_nome():
    with servidor() as (port, dados):
        job = _criar_job(dados)
        _, pagina = _req(port, "GET", f"/job/{job}", AUTH)
        assert 'name="nome"' in pagina and "Seu nome" in pagina


def test_modo_local_funciona_sem_senha_e_recusa_host_externo():
    with servidor() as (port, dados):
        os.environ.pop("APP_SENHA", None)
        APP.SEM_SENHA = True
        try:
            assert _req(port, "GET", "/")[0] == 200
            assert _req(port, "POST", "/gerar", None, "cnj=123")[0] == 400
            assert _req(port, "GET", "/", {"Host": "site-externo.example"})[0] == 403
        finally:
            APP.SEM_SENHA = False


def test_com_senha_configurada_o_acesso_continua_protegido():
    with servidor() as (port, _):
        assert APP.SEM_SENHA is False
        assert _req(port, "GET", "/")[0] == 401


def test_mensagens_de_erro_em_portugues_claro():
    assert "Judit não encontrou" in APP.mensagem_amigavel("BLOCKED_JUDIT_NOT_FOUND: operação não concluída.")
    assert "saldo" in APP.mensagem_amigavel("BLOCKED_LIMIT: geração interrompida; nenhum resumo publicado.")
    assert "não tem permissão" in APP.mensagem_amigavel("BLOCKED_AUTHORIZATION: x")
    assert "foi recusada" in APP.mensagem_amigavel("BLOCKED_AUTH: x")
    assert "JUDIT_API_KEY" in APP.mensagem_amigavel("BLOCKED_JUDIT_AUTHENTICATION: x")
    assert "verificações automáticas" in APP.mensagem_amigavel("FAIL: gate falhou; summary não publicado.")
    assert "Tente novamente" in APP.mensagem_amigavel("algo inesperado")


def test_pagina_de_falha_mostra_explicacao_e_detalhe_tecnico():
    with servidor() as (port, dados):
        job = _criar_job(dados, status="FALHOU")
        meta_arq = dados / job / "meta.json"
        meta = json.loads(meta_arq.read_text())
        meta["motivo"] = "BLOCKED_JUDIT_NOT_FOUND: operação não concluída."
        meta_arq.write_text(json.dumps(meta))
        _, pagina = _req(port, "GET", f"/job/{job}", AUTH)
        assert "A Judit não encontrou este processo" in pagina
        assert "Detalhe técnico" in pagina and "BLOCKED_JUDIT_NOT_FOUND" in pagina

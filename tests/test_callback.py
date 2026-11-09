"""criar_consulta envia callback_url apenas quando JUDIT_CALLBACK_URL está definida."""
from types import SimpleNamespace

from resumo.judit import ClienteJudit

CNJ = "5002363-81.2018.8.21.0033"


class _Cli:
    def __init__(self):
        self.corpos = []

    def post(self, path, json=None):
        self.corpos.append(json)
        return SimpleNamespace(status_code=200, json=lambda: {"request_id": "07ac3b3c-f417-4402-94e6-782813c5a2b1"})


def test_callback_enviado_quando_configurado(monkeypatch):
    monkeypatch.setenv("JUDIT_CALLBACK_URL", "https://exemplo.trycloudflare.com/judit/webhook")
    cli = _Cli()
    ClienteJudit(api_key="x", cliente=cli).criar_consulta(CNJ)
    assert cli.corpos[0]["callback_url"] == "https://exemplo.trycloudflare.com/judit/webhook"


def test_callback_ausente_quando_nao_configurado(monkeypatch):
    monkeypatch.delenv("JUDIT_CALLBACK_URL", raising=False)
    cli = _Cli()
    ClienteJudit(api_key="x", cliente=cli).criar_consulta(CNJ)
    assert "callback_url" not in cli.corpos[0]

"""Webhook da Judit: aceitar evento válido, ignorar outros, rejeitar lixo e não repetir request."""
import uuid
from pathlib import Path

from scripts.servidor_webhook import tratar_evento

RID = str(uuid.UUID("07ac3b3c-f417-4402-94e6-782813c5a2b1"))


def _evento(**kw):
    base = {"event_type": "response_created", "reference_type": "request", "reference_id": RID}
    base.update(kw)
    return base


def test_evento_valido_dispara_processamento(tmp_path):
    status, corpo = tratar_evento(_evento(), tmp_path, disparar=lambda *a: None)
    assert status == 200 and corpo["status"] == "aceito" and corpo["request_id"] == RID


def test_evento_de_outro_tipo_e_ignorado(tmp_path):
    status, corpo = tratar_evento(_evento(event_type="outro"), tmp_path, disparar=lambda *a: None)
    assert status == 200 and corpo["status"] == "ignorado"


def test_reference_id_invalido_retorna_400(tmp_path):
    status, _ = tratar_evento(_evento(reference_id="nao-uuid"), tmp_path, disparar=lambda *a: None)
    assert status == 400


def test_request_ja_processado_nao_repete(tmp_path):
    (tmp_path / f"{RID}.json").write_text("{}", encoding="utf-8")
    status, corpo = tratar_evento(_evento(), tmp_path, disparar=lambda *a: None)
    assert status == 200 and corpo["status"] == "duplicado"

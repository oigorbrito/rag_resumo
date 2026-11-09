import json
from collections import deque

import httpx
import pytest

from resumo.judit import BASE_URL, ClienteJudit, JuditError

REQUEST_ID = "07ac3b3c-f417-4402-94e6-782813c5a2b1"
CNJ = "0009999-99.9999.8.26.9999"
OTHER_CNJ = "0009999-99.9999.8.26.9998"
LAWSUIT = {"code": CNJ, "instance": 1, "secrecy_level": 0, "parties": [], "steps": []}


@pytest.fixture(autouse=True)
def judit_test_key(monkeypatch):
    monkeypatch.setenv("JUDIT_API_KEY", "test-only-key")


def transport_client(responses):
    pending = deque(responses)
    seen = []

    def handler(request):
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, dict(request.url.params), body,
                     request.headers.get("api-key")))
        try:
            result = pending.popleft()
        except IndexError:
            return httpx.Response(500, json={"error": "unexpected mock request"})
        if isinstance(result, BaseException):
            raise result
        status, payload = result
        return httpx.Response(status, json=payload)

    client = httpx.Client(base_url=BASE_URL, headers={"api-key": "test-key"},
                          transport=httpx.MockTransport(handler))
    return client, seen


def status_payload(status="completed", request_id=REQUEST_ID, cnj=CNJ):
    return {"request_id": request_id, "status": status,
            "search": {"search_type": "lawsuit_cnj", "search_key": cnj}}


def lawsuit_item(instance=1, *, response_id="r1", cached=None, cnj=CNJ, request_id=REQUEST_ID):
    item = {"request_id": request_id, "response_id": response_id, "response_type": "lawsuit",
            "response_data": {**LAWSUIT, "code": cnj, "instance": instance}}
    if cached is not None:
        item["cached_response"] = cached
    return item


def test_consulta_cnj_post_poll_get_y_normaliza_resposta():
    import os
    os.environ.pop('JUDIT_CALLBACK_URL', None)  # isola o teste da configuração local
    client, seen = transport_client([
        (201, {"request_id": REQUEST_ID}),
        (200, status_payload("pending")), (200, status_payload("completed")),
        (200, status_payload("completed")),
        (200, {"page_data": [lawsuit_item()]}),
    ])
    try:
        request_id, payload = ClienteJudit(cliente=client).consultar(CNJ, timeout=2, intervalo=0)
    finally:
        client.close()
    assert request_id == REQUEST_ID
    assert payload["page_data"][0]["response_data"]["code"] == CNJ
    assert seen[0][0:2] == ("POST", "/requests")
    assert seen[0][3] == {"search": {"search_type": "lawsuit_cnj", "search_key": CNJ}, "with_attachments": False}
    assert all(call[4] == "test-key" for call in seen)


@pytest.mark.parametrize("state", ["pending", "started", "cancelling"])
def test_estados_intermediarios_aguardam_finalizacao(state):
    client, _ = transport_client([(200, status_payload(state)), (200, status_payload("completed"))])
    try:
        ClienteJudit(cliente=client).aguardar(REQUEST_ID, timeout=2, intervalo=0)
    finally:
        client.close()


def test_timeout_de_polling():
    client, _ = transport_client([(200, status_payload("started"))])
    try:
        with pytest.raises(JuditError) as error:
            ClienteJudit(cliente=client).aguardar(REQUEST_ID, timeout=0, intervalo=0)
    finally:
        client.close()
    assert error.value.category == "timeout"


def test_status_terminal_cancelled():
    client, _ = transport_client([(200, status_payload("cancelled"))])
    try:
        with pytest.raises(JuditError) as error:
            ClienteJudit(cliente=client).aguardar(REQUEST_ID)
    finally:
        client.close()
    assert error.value.category == "cancelled"


@pytest.mark.parametrize(("http_status", "category"), [
    (401, "authentication"), (403, "authorization"), (404, "not_found"), (429, "rate_limit"),
])
def test_erros_http_judit_parametrizados(http_status, category):
    client, _ = transport_client([(http_status, {"error": "response body must not leak"})])
    try:
        with pytest.raises(JuditError) as error:
            ClienteJudit(cliente=client).recuperar(REQUEST_ID)
    finally:
        client.close()
    assert error.value.status_code == http_status
    assert error.value.category == category
    assert "response body" not in str(error.value)


def test_transport_error_is_classified_without_retry():
    client, seen = transport_client([httpx.ConnectTimeout("timeout")])
    try:
        with pytest.raises(JuditError) as error:
            ClienteJudit(cliente=client).recuperar(REQUEST_ID)
    finally:
        client.close()
    assert error.value.category == "transport"
    assert len(seen) == 1


def test_request_id_inexistente_404():
    client, _ = transport_client([(404, {"error": "not found"})])
    try:
        with pytest.raises(JuditError) as error:
            ClienteJudit(cliente=client).recuperar(REQUEST_ID)
    finally:
        client.close()
    assert error.value.status_code == 404
    assert error.value.category == "not_found"


def test_cnj_invalido_nao_faz_http():
    client, seen = transport_client([])
    try:
        with pytest.raises(ValueError, match="CNJ inválido"):
            ClienteJudit(cliente=client).criar_consulta("not-cnj")
    finally:
        client.close()
    assert not seen


def test_paginas_deduplicadas_priorizam_nao_cache_e_ordenam_instancia():
    primeira = [lawsuit_item(1, response_id=f"duplicata-{n}") for n in range(100)]
    segunda = [lawsuit_item(1, response_id="cached", cached=True),
               lawsuit_item(1, response_id="fresh", cached=False),
               lawsuit_item(2, response_id="instance-2")]
    client, seen = transport_client([
        (200, status_payload()),
        (200, {"page_data": primeira}), (200, {"page_data": segunda}),
    ])
    try:
        payload = ClienteJudit(cliente=client).recuperar(REQUEST_ID)
    finally:
        client.close()
    assert [x["response_data"]["instance"] for x in payload["page_data"]] == [1, 2]
    assert payload["page_data"][0]["response_id"] == "fresh"
    assert [x[2]["page"] for x in seen if x[1] == "/responses"] == ["1", "2"]


def test_resposta_sem_lawsuit_e_incompleta_rejeitada():
    client, _ = transport_client([(200, status_payload()), (200, {"page_data": [{"response_type": "lawsuit"}]})])
    try:
        with pytest.raises(JuditError, match="response_data/code"):
            ClienteJudit(cliente=client).recuperar(REQUEST_ID)
    finally:
        client.close()


def test_identidade_consulta_e_processo_validada():
    client, _ = transport_client([(200, status_payload()), (200, {"page_data": [lawsuit_item(cnj=OTHER_CNJ)]})])
    try:
        with pytest.raises(JuditError) as error:
            ClienteJudit(cliente=client).recuperar(REQUEST_ID)
    finally:
        client.close()
    assert error.value.category == "identity_mismatch"


def test_request_id_e_identidade_da_resposta_validada():
    client, _ = transport_client([(200, status_payload(request_id="00000000-0000-0000-0000-000000000000"))])
    try:
        with pytest.raises(JuditError) as error:
            ClienteJudit(cliente=client).recuperar(REQUEST_ID)
    finally:
        client.close()
    assert error.value.category == "identity_mismatch"


def test_item_de_response_de_outro_request_id_rejeitado():
    client, _ = transport_client([
        (200, status_payload()),
        (200, {"page_data": [lawsuit_item(request_id="00000000-0000-0000-0000-000000000000")]})
    ])
    try:
        with pytest.raises(JuditError) as error:
            ClienteJudit(cliente=client).recuperar(REQUEST_ID)
    finally:
        client.close()
    assert error.value.category == "identity_mismatch"


def test_interrupcao_nao_e_repetida_nem_convertida():
    client, seen = transport_client([KeyboardInterrupt()])
    try:
        with pytest.raises(KeyboardInterrupt):
            ClienteJudit(cliente=client).recuperar(REQUEST_ID)
    finally:
        client.close()
    assert len(seen) == 1

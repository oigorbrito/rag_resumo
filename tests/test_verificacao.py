"""Verificação item a item: cada data e valor do texto com status e evidência (offline)."""
import json
from pathlib import Path

from resumo import checagem

FIXTURE = Path(__file__).parent / "fixtures" / "judit_exemplo.json"


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _por_texto(itens):
    return {i["texto"]: i for i in itens}


def test_data_de_movimento_vem_com_a_evidencia_do_movimento():
    texto = "Em 02/09/2026 os autos foram remetidos."
    itens = _por_texto(checagem.itens_verificados(texto, _payload()))
    assert itens["02/09/2026"]["status"] == "confirmado"
    assert itens["02/09/2026"]["evidencia"].startswith("Movimento:")


def test_valor_da_causa_confirmado():
    itens = _por_texto(checagem.itens_verificados("O valor da causa é de R$ 30.000,00.", _payload()))
    assert itens["R$ 30.000,00"]["status"] == "confirmado"
    assert "Valor da causa" in itens["R$ 30.000,00"]["evidencia"]


def test_item_inexistente_e_marcado_como_nao_encontrado():
    texto = "Em 01/01/1999 houve algo, no valor de R$ 123.456,78."
    itens = _por_texto(checagem.itens_verificados(texto, _payload()))
    assert itens["01/01/1999"]["status"] == "nao_encontrado"
    assert itens["R$ 123.456,78"]["status"] == "nao_encontrado"


def test_data_calculada_pelo_sistema_e_derivada():
    fatos = [{"fim_do_prazo": "04/02/2031"}]
    itens = _por_texto(checagem.itens_verificados("O prazo terminou em 04/02/2031.", _payload(), fatos))
    assert itens["04/02/2031"]["status"] == "derivado"


def test_itens_repetidos_aparecem_uma_vez_e_na_ordem_do_texto():
    texto = "Em 02/09/2026 e de novo em 02/09/2026; valor R$ 30.000,00."
    itens = checagem.itens_verificados(texto, _payload())
    assert [i["texto"] for i in itens] == ["02/09/2026", "R$ 30.000,00"]


def test_consistente_com_verificar():
    texto = "Em 01/01/1999 houve algo."
    assert checagem.verificar(texto, _payload())
    assert checagem.itens_verificados(texto, _payload())[0]["status"] == "nao_encontrado"


def test_data_citada_dentro_do_texto_de_um_movimento_mostra_esse_movimento():
    payload = {"code": "5000395-40.2023.8.21.0033", "parties": [], "steps": [
        {"step_date": "2023-06-26T15:00:00.000Z", "content": "25 - TRANSITADO EM JULGADO - DATA: 24/04/2023"}]}
    itens = _por_texto(checagem.itens_verificados("O trânsito em julgado ocorreu em 24/04/2023.", payload))
    assert itens["24/04/2023"]["status"] == "confirmado"
    assert itens["24/04/2023"]["evidencia"].startswith("Movimento (cita a data):")
    assert "TRANSITADO EM JULGADO" in itens["24/04/2023"]["evidencia"]

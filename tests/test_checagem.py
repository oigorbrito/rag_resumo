"""Checagem de fidelidade: datas e valores do resumo precisam existir nos dados de origem."""
import json
from pathlib import Path

from resumo import checagem

FIXTURE = Path(__file__).parent / "fixtures" / "judit_exemplo.json"


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_data_existente_nos_dados_passa():
    datas = sorted(checagem._datas_nos_dados(_payload()))
    assert datas, "fixture precisa ter ao menos uma data"
    assert checagem.verificar(f"Em {datas[0]} houve um ato.", _payload()) == []


def test_data_inventada_e_bloqueada():
    erros = checagem.verificar("Em 31/02/2099 houve audiência.", _payload())
    assert any("31/02/2099" in e for e in erros)


def test_valor_inventado_e_bloqueado():
    erros = checagem.verificar("O valor da causa é R$ 987.654,32.", _payload())
    assert any("987.654,32" in e for e in erros)


def test_valor_real_passa():
    valores = [v for v in checagem._valores_nos_dados(_payload()) if v >= 1000]
    assert valores, "fixture precisa ter um valor monetário"
    br = f"{valores[0]:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    assert checagem.verificar(f"O valor é R$ {br}.", _payload()) == []

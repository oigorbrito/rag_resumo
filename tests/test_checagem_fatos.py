"""Datas derivadas pelo parser (presentes nos fatos enviados ao modelo) não devem ser bloqueadas."""
from resumo import checagem


def test_data_derivada_nos_fatos_passa():
    payload = {"foo": "nada de datas aqui"}
    fatos = [{"prazo_fim": "04/02/2024"}]
    assert checagem.verificar("O prazo termina em 04/02/2024.", payload, fatos) == []


def test_data_fora_de_todas_as_fontes_continua_bloqueada():
    payload = {"foo": "x"}
    fatos = [{"prazo_fim": "04/02/2024"}]
    erros = checagem.verificar("Em 31/12/2030 houve audiência.", payload, fatos)
    assert any("31/12/2030" in e for e in erros)

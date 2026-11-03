from resumo.generator import GeradorTemplate
from resumo.parser import extrair_fatos
from scripts.gerar_datajud import converter_datajud, formatar_cnj


def _fonte_fake():
    return {
        "numeroProcesso": "12345678901234567890",
        "tribunal": "TRF1",
        "classe": {"codigo": 1, "nome": "Classe Fictícia"},
        "assuntos": [[{"codigo": 2, "nome": "Assunto Fictício"}]],
        "orgaoJulgador": {"codigo": 3, "nome": "Vara Fictícia"},
        "dataAjuizamento": "20200501120000",
        "nivelSigilo": 0,
        "movimentos": [
            {"nome": "Distribuição", "dataHora": "20200502120000"},
            {"nome": "Juntada de documento", "dataHora": "20200503120000"},
        ],
    }


def test_converter_mantem_campos_datajud_sem_inventar_partes():
    payload = converter_datajud(_fonte_fake())
    fatos = extrair_fatos(payload["response_data"])

    assert formatar_cnj("12345678901234567890") == "1234567-89.0123.4.56.7890"
    assert fatos.cnj == "1234567-89.0123.4.56.7890"
    assert fatos.tribunal_sigla == "TRF1"
    assert fatos.classes[0].nome == "Classe Fictícia"
    assert fatos.classes[0].codigo == "1"
    assert fatos.assuntos[0].nome == "Assunto Fictício"
    assert fatos.orgao_julgador == "Vara Fictícia"
    assert fatos.data_ajuizamento == "01/05/2020"
    assert fatos.data_distribuicao == "02/05/2020"
    assert [(m.texto, m.data) for m in fatos.movimentos] == [
        ("Distribuição", "02/05/2020"),
        ("Juntada de documento", "03/05/2020"),
    ]
    assert fatos.partes == []


def test_template_nao_inventa_partes_ou_citacao_sem_dados():
    fatos = extrair_fatos(converter_datajud(_fonte_fake())["response_data"])
    secoes = GeradorTemplate().gerar(fatos, {})
    introducao = " ".join(secoes["introducao"])

    assert "proposta por" not in introducao
    assert "em face de" not in introducao
    assert "citação da parte ré" not in introducao
    assert "A data de ajuizamento registrada é" in introducao

"""Garante que a lista de dependências cobre o que o programa precisa em máquina limpa (Windows e Docker)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_requirements_inclui_tzdata_para_windows_e_docker():
    linhas = [l.strip().lower() for l in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()]
    assert any(l.startswith("tzdata") for l in linhas)


def test_iniciar_bat_confere_o_fuso_horario_e_usa_crlf():
    dados = (ROOT / "iniciar.bat").read_bytes()
    assert b"zoneinfo" in dados and b"\r\n" in dados


def test_iniciar_bat_so_abre_o_navegador_quando_o_servidor_responde():
    dados = (ROOT / "iniciar.bat").read_bytes()
    assert b"TcpClient" in dados and b"Start-Sleep -Seconds 3" not in dados


def test_readme_explica_o_inicio_rapido_pelo_iniciar_bat():
    texto = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Início rápido" in texto and "iniciar.bat" in texto
    assert texto.index("Início rápido") < texto.index("## Como funciona")


def _variaveis_lidas():
    padrao = re.compile(r"""(?:os\.environ(?:\.get)?\(|os\.getenv\(|os\.environ\[)\s*["']([A-Z][A-Z_]+)["']""")
    achadas = set()
    for pasta in ("scripts", "resumo"):
        for arq in (ROOT / pasta).rglob("*.py"):
            achadas.update(padrao.findall(arq.read_text(encoding="utf-8")))
    return achadas


def test_todas_as_variaveis_de_ambiente_estao_documentadas():
    lidas = _variaveis_lidas()
    assert {"ANTHROPIC_API_KEY", "JUDIT_API_KEY", "APP_SENHA", "HOST", "PORT", "RESUMO_DADOS_DIR"} <= lidas
    exemplo = (ROOT / ".env.example").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    faltando = sorted(v for v in lidas if v not in exemplo or v not in readme)
    assert not faltando, f"variáveis sem documentação: {faltando}"

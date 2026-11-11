"""Garante que a lista de dependências cobre o que o programa precisa em máquina limpa (Windows e Docker)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_requirements_inclui_tzdata_para_windows_e_docker():
    linhas = [l.strip().lower() for l in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()]
    assert any(l.startswith("tzdata") for l in linhas)


def test_iniciar_bat_confere_o_fuso_horario_e_usa_crlf():
    dados = (ROOT / "iniciar.bat").read_bytes()
    assert b"zoneinfo" in dados and b"\r\n" in dados

@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python nao foi encontrado. Instale o Python 3.12 e tente de novo.
  pause
  exit /b 1
)
python -c "import anthropic, dotenv, httpx, numpy, zoneinfo; zoneinfo.ZoneInfo('America/Sao_Paulo')" >nul 2>nul
if errorlevel 1 (
  echo Primeira execucao: instalando as dependencias. Isso pode levar alguns minutos.
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Falha ao instalar as dependencias. Verifique a conexao com a internet.
    pause
    exit /b 1
  )
)
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Foi criado o arquivo .env. Preencha ANTHROPIC_API_KEY e JUDIT_API_KEY, salve e feche o Bloco de Notas.
  start /wait notepad ".env"
)
set HOST=127.0.0.1
set PORT=8000
echo Abrindo http://localhost:%PORT% - feche esta janela para encerrar o programa.
start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://localhost:%PORT%'"
python scripts\app_web.py
pause

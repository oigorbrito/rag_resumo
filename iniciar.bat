@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python nao foi encontrado. Instale o Python 3.12 e tente de novo.
  pause
  exit /b 1
)
if not exist ".env" (
  echo Arquivo .env nao encontrado. Copie .env.example para .env e preencha ANTHROPIC_API_KEY e JUDIT_API_KEY.
  pause
  exit /b 1
)
set HOST=127.0.0.1
set PORT=8000
echo Abrindo http://localhost:%PORT% - feche esta janela para encerrar o programa.
start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://localhost:%PORT%'"
python scripts\app_web.py
pause

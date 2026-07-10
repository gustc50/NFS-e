@echo off
rem Inicia o NFS-e Monitor (Windows)
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Python nao encontrado. Instale em https://www.python.org/downloads/
    pause
    exit /b 1
)
if not exist .venv (
    echo Criando ambiente virtual...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
python run.py %*
pause

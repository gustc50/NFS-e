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
)
call .venv\Scripts\activate.bat
rem Instala/atualiza as dependencias a cada inicio (rapido quando ja estao em dia)
echo Verificando dependencias...
python -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Falha ao instalar as dependencias. Verifique a conexao com a internet.
    pause
    exit /b 1
)
python run.py %*
pause

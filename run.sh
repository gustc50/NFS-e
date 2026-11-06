#!/usr/bin/env bash
# Inicia o NFS-e Monitor (Linux/macOS)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "Criando ambiente virtual..."
    python3 -m venv .venv
fi
# Instala/atualiza as dependências a cada início (rápido quando já estão em dia)
./.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
exec ./.venv/bin/python run.py "$@"

#!/usr/bin/env bash
# Inicia o NFS-e Monitor (Linux/macOS)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "Criando ambiente virtual..."
    python3 -m venv .venv
    ./.venv/bin/pip install -r requirements.txt
fi
exec ./.venv/bin/python run.py "$@"

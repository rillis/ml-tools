#!/usr/bin/env bash
# Abre o menu do ml-tools com o Python da venv da pasta.
# Os módulos que o menu chama (margem, skus, envio, comum) usam o mesmo Python.
# Se a venv não existir, cria e instala o requirements.txt antes.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [ ! -x .venv/bin/python ]; then
    echo "Criando a venv..."
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/python menu.py "$@"

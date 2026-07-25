#!/usr/bin/env bash
# Start the local Kiro gateway.
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .venv/bin/activate ]]; then
    source .venv/bin/activate
elif [[ -f venv/bin/activate ]]; then
    source venv/bin/activate
else
    echo "No virtual environment found. Create one with: python3 -m venv .venv" >&2
    exit 2
fi

if [[ -z "${KIRO_GATEWAY_KEY:-}" ]]; then
    echo "KIRO_GATEWAY_KEY is required. Export a strong local API key before starting." >&2
    exit 2
fi

# Loopback is the secure default because this process can use the local Kiro
# account. Public binds must be an explicit operator decision.
export KIRO_GATEWAY_HOST="${KIRO_GATEWAY_HOST:-127.0.0.1}"
export KIRO_GATEWAY_PORT="${KIRO_GATEWAY_PORT:-18789}"
export KIRO_REGION="${KIRO_REGION:-us-east-1}"
export KIRO_RUNTIME_TIMEOUT="${KIRO_RUNTIME_TIMEOUT:-120}"

exec python3 kiro_gateway.py

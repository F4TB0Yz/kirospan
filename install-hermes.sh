#!/usr/bin/env bash
# Install KiroSpan globally for Hermes Agent on macOS.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${repo_dir}/scripts/install_hermes.py" "$@"

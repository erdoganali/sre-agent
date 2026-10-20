#!/usr/bin/env bash
# Entegrasyon testleri (yalnızca okuma). Kubernetes'e read-only SA token'ıyla bağlanır.
# Kullanım: scripts/test-integration.sh <kube-context> <monitoring-ns> <agent-ns> <serviceaccount>
set -euo pipefail
source "$(dirname "$0")/lib/it-env.sh"
it_env "$@"
uv run pytest -m integration -o addopts="" -q -s tests/integration/test_gke_adapters.py tests/integration/test_vectordb_pack.py

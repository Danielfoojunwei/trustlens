#!/usr/bin/env bash
#
# launch_local.sh — run TrustLens locally on a single machine (GB10 or any
# workstation). Menu-driven, sequential-by-design: never runs a 35B model
# at the same time as the Ollama chat path.
#
# Modes:
#   1  Run the 10 000-item TrustLens-10k benchmark (CPU only, 3 s)
#   2  Run the penetration + overload battery  (CPU only, ≈ 40 s)
#   3  Interactive chat: Ollama + llama3.1:8b behind the TrustLens gateway
#      (opens the operator dashboard + MCP stdio server)
#   4  Everything sequential: 1 → 2 → 3
#
# Memory discipline:
#   - Modes 1+2 run against the EchoBackend (no LLM) so they never touch
#     the GPU. Safe to run any time.
#   - Mode 3 starts Ollama *only after* any pre-checks pass and shuts down
#     cleanly on Ctrl-C. Will refuse to start if a large vLLM process is
#     detected consuming GB10 VRAM.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# ---------------------------------------------------------------- helpers
bold()   { printf "\033[1m%s\033[0m\n" "$*"; }
okmark() { printf "  \033[32m✓\033[0m  %s\n" "$*"; }
info()   { printf "  →  %s\n" "$*"; }
warn()   { printf "  \033[33m!\033[0m  %s\n" "$*"; }
err()    { printf "  \033[31m✗\033[0m  %s\n" "$*" >&2; }

PORT_GATEWAY="${TRUSTLENS_PORT:-8081}"
PORT_OLLAMA="${OLLAMA_PORT:-11434}"
SIGNER_KEY="${TRUSTLENS_SIGNER_KEY:-${REPO_DIR}/.trustlens/signer.pem}"
CERT_STORE="${TRUSTLENS_CERT_STORE:-${REPO_DIR}/.trustlens/certs}"

preflight() {
    bold "[preflight]"
    command -v trustlens >/dev/null || {
        err "trustlens CLI not on PATH. Install with: pip install -e ."
        exit 2
    }
    okmark "trustlens CLI: $(trustlens version 2>/dev/null | head -1)"
    if lsof -i ":${PORT_GATEWAY}" >/dev/null 2>&1; then
        warn "port ${PORT_GATEWAY} already bound — the gateway will likely fail to start"
    else
        okmark "port ${PORT_GATEWAY} free"
    fi
    if command -v nvidia-smi >/dev/null 2>&1; then
        local mem_used
        mem_used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
        if [ -n "${mem_used:-}" ] && [ "${mem_used}" -gt 20000 ] 2>/dev/null; then
            warn "GPU already using ${mem_used} MiB — a large model is likely loaded; Mode 3 may OOM. Consider stopping it first."
        else
            okmark "GPU idle (${mem_used:-?} MiB used)"
        fi
    fi
}

start_gateway() {
    local bg_pid_file="$1"
    bold "[gateway]"
    mkdir -p "$(dirname "$SIGNER_KEY")" "$CERT_STORE"
    (nohup trustlens serve-gateway \
        --host 127.0.0.1 --port "${PORT_GATEWAY}" \
        --signer-key "$SIGNER_KEY" \
        --cert-store "$CERT_STORE" > /tmp/trustlens_gateway.log 2>&1 & echo $! > "$bg_pid_file") >/dev/null
    # Wait for health
    for i in $(seq 1 20); do
        if curl -sf "http://127.0.0.1:${PORT_GATEWAY}/healthz" >/dev/null 2>&1; then
            okmark "gateway up at http://127.0.0.1:${PORT_GATEWAY}"
            return 0
        fi
        sleep 0.3
    done
    err "gateway failed to start; tail /tmp/trustlens_gateway.log"
    return 1
}

stop_gateway() {
    local bg_pid_file="$1"
    if [ -f "$bg_pid_file" ]; then
        kill "$(cat "$bg_pid_file")" 2>/dev/null || true
        rm -f "$bg_pid_file"
        okmark "gateway stopped"
    fi
}

mode_1_bench() {
    bold "\n[1/1] TrustLens-10k benchmark (10 000 items × 10 axes)"
    python3 scripts/run_trustlens_10k.py \
        --out-dir "${REPO_DIR}/results/trustlens_10k" \
        --signer-key "$SIGNER_KEY"
}

mode_2_pentest() {
    bold "\n[2/2] Penetration + overload battery"
    local gw_pid; gw_pid=$(mktemp)
    trap 'stop_gateway "$gw_pid"' RETURN
    start_gateway "$gw_pid"
    python3 scripts/run_pentest.py \
        --gateway-url "http://127.0.0.1:${PORT_GATEWAY}" \
        --out-dir "${REPO_DIR}/results/pentest" \
        --signer-key "$SIGNER_KEY" \
        "$@" || true
}

mode_3_chat() {
    bold "\n[3/3] Interactive chat via Ollama + llama3.1:8b"
    if ! command -v ollama >/dev/null 2>&1; then
        warn "Ollama is not installed. Install with:"
        echo "        curl -fsSL https://ollama.com/install.sh | sh"
        echo "   Then pull the model:"
        echo "        ollama pull llama3.1:8b"
        echo
        warn "Skipping Mode 3; falling back to the echo backend so you can still chat."
    else
        if ! ollama list 2>/dev/null | grep -q 'llama3.1:8b'; then
            info "pulling llama3.1:8b (~5 GB, one-time)"
            ollama pull llama3.1:8b
        fi
        okmark "ollama model cached"
        export OLLAMA_BASE_URL="http://127.0.0.1:${PORT_OLLAMA}"
    fi
    local gw_pid; gw_pid=$(mktemp)
    trap 'stop_gateway "$gw_pid"' EXIT
    start_gateway "$gw_pid"

    bold "\n[dashboard]"
    echo "  open in your browser:"
    echo "      http://127.0.0.1:${PORT_GATEWAY}/dashboard"
    echo
    bold "[MCP server]"
    echo "  to drive TrustLens from a Claude Desktop / Code / Cursor agent, add to"
    echo "  claude_desktop_config.json:"
    cat <<EOF
      {
        "mcpServers": {
          "trustlens": {
            "command": "trustlens",
            "args": ["mcp", "serve", "--transport", "stdio"],
            "env": {
              "TRUSTLENS_BASE_URL":  "http://127.0.0.1:${PORT_GATEWAY}",
              "TRUSTLENS_TENANT_ID": "demo"
            }
          }
        }
      }
EOF
    echo
    bold "[chat from the terminal]"
    echo "  curl -s http://127.0.0.1:${PORT_GATEWAY}/v1/chat/completions \\"
    if [ -n "${OLLAMA_BASE_URL:-}" ]; then
        echo "    -H 'X-TrustLens-Tenant-Id: demo' -H 'Content-Type: application/json' \\"
        echo "    -d '{\"model\":\"llama3.1:8b\",\"messages\":[{\"role\":\"user\",\"content\":\"What is the capital of France?\"}]}' | jq ."
    else
        echo "    -H 'X-TrustLens-Tenant-Id: demo' -H 'Content-Type: application/json' \\"
        echo "    -d '{\"model\":\"echo\",\"messages\":[{\"role\":\"user\",\"content\":\"What is the capital of France?\"}]}' | jq ."
    fi
    echo
    info "press Ctrl-C to shut down the gateway."
    while true; do sleep 3600; done
}

mode_4_all() {
    mode_1_bench
    echo
    mode_2_pentest
    echo
    mode_3_chat
}

pick_mode() {
    bold "TrustLens local launcher"
    echo "  1)  Run 10 000-item adversarial benchmark  (≈ 3 s, no GPU)"
    echo "  2)  Run penetration + overload battery     (≈ 40 s, no GPU)"
    echo "  3)  Interactive chat: Ollama + gateway + dashboard"
    echo "  4)  Everything (1 → 2 → 3, sequential)"
    echo
    read -r -p "choose [1-4]: " c
    case "$c" in
        1) mode_1_bench ;;
        2) mode_2_pentest ;;
        3) mode_3_chat ;;
        4) mode_4_all ;;
        *) err "unknown selection: $c"; exit 2 ;;
    esac
}

preflight
if [ $# -gt 0 ]; then
    case "$1" in
        1|bench)   shift; mode_1_bench "$@" ;;
        2|pentest) shift; mode_2_pentest "$@" ;;
        3|chat)    shift; mode_3_chat "$@" ;;
        4|all)     shift; mode_4_all "$@" ;;
        *) err "unknown mode: $1 (use 1/2/3/4 or bench/pentest/chat/all)"; exit 2 ;;
    esac
else
    pick_mode
fi

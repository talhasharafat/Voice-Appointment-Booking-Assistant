#!/usr/bin/env bash
# Canonical way to run the clinic LiveKit voice agent (exactly one worker).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv-agent/bin/python ]]; then
  echo "Missing .venv-agent. Create it with Python ≥3.10 and:"
  echo "  python3.11 -m venv .venv-agent && .venv-agent/bin/pip install -r voice_agent/requirements.txt"
  exit 1
fi

# Drop stale shell exports that override .env.
unset GROQ_API_KEY GROQ_STT_MODEL GROQ_LLM_MODEL GROQ_LLM_MODELS || true
unset FISH_API_KEY FISH_TTS_MODEL FISH_BASE_URL FISH_VOICE_ID FISH_SAMPLE_RATE FISH_RESPONSE_FORMAT || true
unset VOICE_AGENT_PORT VOICE_MAX_CHAT_ITEMS || true

set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a

export VOICE_AGENT_PORT="${VOICE_AGENT_PORT:-0}"

if [[ -z "${GROQ_API_KEY:-}" ]]; then
  echo "WARNING: GROQ_API_KEY is empty — set it in .env (https://console.groq.com/keys)"
fi
if [[ -z "${FISH_API_KEY:-}" ]]; then
  echo "WARNING: FISH_API_KEY is empty — set it in .env (https://fish.audio)"
fi

echo "Starting clinic-booking agent (LiveKit + Groq STT/LLM + Django tools + Fish TTS). Ctrl+C stops it."
exec .venv-agent/bin/python -m voice_agent.pipeline start

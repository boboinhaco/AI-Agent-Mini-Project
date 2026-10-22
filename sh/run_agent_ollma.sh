#!/usr/bin/env bash
# 예전 이름(오타) 호환 — 실제 스크립트는 run_agent_ollama.sh
exec bash "$(dirname "$0")/run_agent_ollama.sh" "$@"

#!/bin/zsh
# One-time setup for Vlog Studio (fully local).
set -e
HERE=${0:A:h}
cd "$HERE"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
PY=""
for c in python3.13 python3.12 python3.11 python3.14 /opt/homebrew/bin/python3 python3; do
  if command -v $c >/dev/null 2>&1; then PY=$(command -v $c); break; fi
done
[[ -n "$PY" ]] || { echo "Python 3 not found. Install: brew install python"; exit 1; }
echo "Using $PY ($($PY --version))"
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found. Install it with: brew install ffmpeg"; exit 1
fi
[[ -d .venv ]] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
[[ -f vlog.toml ]] || cp vlog.example.toml vlog.toml
echo "\n✓ Setup complete. Next:"
echo "  ./vlog doctor          # check everything"
echo "  ./vlog make            # automatic mode (no AI model needed)"
echo "  # with a local model: install Ollama (https://ollama.com), then e.g."
echo "  #   ollama pull qwen2.5vl:7b      and set [llm] enabled = true in vlog.toml"

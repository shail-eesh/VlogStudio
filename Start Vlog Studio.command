#!/bin/zsh
# Double-click to start Vlog Studio in a Terminal window (close the window to stop it).
cd "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
[[ -x .venv/bin/python ]] || ./setup.sh
./vlog ui

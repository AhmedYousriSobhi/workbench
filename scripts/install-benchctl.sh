#!/usr/bin/env bash
# Put `benchctl` on your PATH, no sudo and no venv activation (issue 924):  ./scripts/install-benchctl.sh [--uninstall]
# It symlinks scripts/benchctl into $BENCHCTL_BIN_DIR (default ~/.local/bin); the wrapper finds the repo's .venv itself.
set -eu
repo="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
bin="${BENCHCTL_BIN_DIR:-$HOME/.local/bin}"
link="$bin/benchctl"
if [ "${1:-}" = "--uninstall" ]; then
  if [ -L "$link" ] && [ "$(readlink -f "$link")" = "$repo/scripts/benchctl" ]; then rm "$link"; echo "removed $link"; else echo "nothing of ours at $link"; fi
  exit 0
fi
mkdir -p "$bin"
if [ -e "$link" ] || [ -L "$link" ]; then
  if [ "$(readlink -f "$link")" = "$repo/scripts/benchctl" ]; then echo "already installed: $link"; else
    echo "refusing to replace $link: it is not this repo's benchctl (remove it first)" >&2; exit 1; fi
else
  ln -s "$repo/scripts/benchctl" "$link"; echo "installed $link"
fi
case ":$PATH:" in *":$bin:"*) ;; *) echo "add $bin to PATH:  echo 'export PATH=\"$bin:\$PATH\"' >> ~/.bashrc" ;; esac
echo "tab completion (one line in your rc file, undo with remove):  benchctl completion install"
echo "try:  benchctl status"

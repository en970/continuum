#!/bin/sh
# Continuum installer. Works from wherever the repo was cloned.
#
#   ./install.sh              install
#   ./install.sh --uninstall  remove everything it added
#
# What it does:
#   - links `continuum` into a directory on your PATH
#   - installs the Claude Code skill (if ~/.claude/skills exists)
#   - adds a shell hook that revives the watcher if it ever dies
#   - builds the menu app (macOS only, needs swiftc)
#   - starts the watcher

set -eu

REPO=$(cd "$(dirname "$0")" && pwd)
PY=${PYTHON:-$(command -v python3 || echo /usr/bin/python3)}
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/continuum"
HOOK_MARK="# continuum: revive the watcher on new shells"

# Pick a bin directory that is already on PATH where possible
pick_bindir() {
    for d in "$HOME/.local/bin" "$HOME/bin"; do
        case ":$PATH:" in *":$d:"*) echo "$d"; return;; esac
    done
    echo "$HOME/.local/bin"
}
BIN_DIR=$(pick_bindir)

rc_file() {
    case "${SHELL##*/}" in
        zsh)  echo "$HOME/.zshrc" ;;
        bash) [ -f "$HOME/.bashrc" ] && echo "$HOME/.bashrc" || echo "$HOME/.bash_profile" ;;
        *)    echo "" ;;
    esac
}

uninstall() {
    echo "Removing Continuum..."
    "$PY" "$REPO/continuum.py" stop >/dev/null 2>&1 || true
    rm -f "$BIN_DIR/continuum"
    [ -L "$HOME/.claude/skills/continuum" ] && rm -f "$HOME/.claude/skills/continuum" || true
    rm -rf "/Applications/Continuum.app"
    RC=$(rc_file)
    if [ -n "$RC" ] && [ -f "$RC" ] && grep -qF "$HOOK_MARK" "$RC"; then
        # Drop the marker line and the two lines of hook that follow it
        awk -v mark="$HOOK_MARK" '
            index($0, mark) { skip = 3 }
            skip > 0 { skip--; next }
            { print }
        ' "$RC" > "$RC.continuum.tmp" && mv "$RC.continuum.tmp" "$RC"
        echo "  removed the shell hook from $RC"
    fi
    echo "Done. Your settings in $STATE_DIR were left alone."
    echo "Delete them too with:  rm -rf \"$STATE_DIR\""
    exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall

echo "Continuum installer"
echo "  repo   : $REPO"
echo "  python : $PY"
echo

# --- sanity ---------------------------------------------------------------
"$PY" - <<'EOF' || { echo "Python 3.8+ is required." >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info >= (3, 8) else 1)
EOF

if command -v tmux >/dev/null; then
    echo "  panes  : tmux found (preferred)"
elif [ "$(uname)" = "Darwin" ]; then
    echo "  panes  : no tmux; will use macOS Terminal.app"
else
    echo "  panes  : WARNING — no tmux found and this is not macOS."
    echo "           Continuum has nothing to read. Install tmux first."
fi
echo

# --- cli ------------------------------------------------------------------
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/continuum" <<EOF
#!/bin/sh
exec "$PY" "$REPO/continuum.py" "\$@"
EOF
chmod +x "$BIN_DIR/continuum"
echo "installed: $BIN_DIR/continuum"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "  note: $BIN_DIR is not on your PATH — add it to use \`continuum\` directly." ;;
esac

# --- skill ----------------------------------------------------------------
if [ -d "$HOME/.claude/skills" ]; then
    ln -sfn "$REPO/skill" "$HOME/.claude/skills/continuum"
    echo "installed: Claude Code skill (say \"use continuum\" in any session)"
fi

# --- shell hook -----------------------------------------------------------
RC=$(rc_file)
if [ -n "$RC" ]; then
    if grep -qF "$HOOK_MARK" "$RC" 2>/dev/null; then
        echo "already present: shell hook in $RC"
    else
        {
            echo ""
            echo "$HOOK_MARK"
            echo "[ -n \"\${PS1:-}\" ] && [ -x \"$BIN_DIR/continuum\" ] && (\"$BIN_DIR/continuum\" ensure >/dev/null 2>&1 &)"
        } >> "$RC"
        echo "installed: shell hook in $RC"
    fi
fi

# --- app ------------------------------------------------------------------
if [ "$(uname)" = "Darwin" ] && command -v swiftc >/dev/null; then
    if sh "$REPO/app/build.sh" >/dev/null 2>&1; then
        echo "installed: /Applications/Continuum.app"
    else
        echo "skipped: app build failed (run app/build.sh to see why)"
    fi
elif [ "$(uname)" = "Darwin" ]; then
    echo "skipped: app needs swiftc (install Xcode Command Line Tools)"
fi

# --- start ----------------------------------------------------------------
mkdir -p "$STATE_DIR"
"$PY" "$REPO/continuum.py" start || true

echo
echo "Ready."
echo "  continuum status    what is being watched"
echo "  continuum scan      what is detected right now"

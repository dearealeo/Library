#!/bin/bash
set -euo pipefail
IFS=$'\n\t'
umask 077

readonly LOGFILE="$HOME/Library/Logs/brew_upgrade.log"
readonly LOCK_F="/tmp/brew_upgrade.lock"
readonly BREW_BIN="/opt/homebrew/bin/brew"
readonly MAX_LOG_SIZE=1048576
readonly MAX_ROTATED_LOGS=1

log() { printf '[%s] %s\n' "$(date +%FT%T)" "$*" >&2; }

handle_error() {
    log "Error at ${BASH_SOURCE[0]}:${BASH_LINENO[0]}"
    exit 1
}

rotate_log() {
    mkdir -p "${LOGFILE%/*}"
    if [[ -f "$LOGFILE" ]]; then
        local size=0
        if stat -f %z "$LOGFILE" 2>/dev/null; then
            size=$(stat -f %z "$LOGFILE")
        elif stat -c %s "$LOGFILE" 2>/dev/null; then
            size=$(stat -c %s "$LOGFILE")
        else
            size=$(wc -c < "$LOGFILE" 2>/dev/null || echo 0)
        fi
        if [[ $size -ge $MAX_LOG_SIZE ]]; then
            for ((i=$MAX_ROTATED_LOGS; i>=1; i--)); do
                [[ $i -eq $MAX_ROTATED_LOGS && -f "$LOGFILE.$i" ]] && rm -f "$LOGFILE.$i"
                [[ -f "$LOGFILE.$((i-1))" ]] && mv -f "$LOGFILE.$((i-1))" "$LOGFILE.$i"
            done
            mv -f "$LOGFILE" "$LOGFILE.1"
        fi
    fi
}

trap handle_error ERR
trap 'rm -f "$LOCK_F"; exit' EXIT INT TERM HUP

if command -v flock >/dev/null 2>&1; then
    exec 200>"$LOCK_F"
    flock -n 200 || exit 0
else
    [[ -f "$LOCK_F" ]] && { 
        pid=$(cat "$LOCK_F" 2>/dev/null || true)
        if [[ -n "$pid" ]]; then
            if ps -p "$pid" &>/dev/null; then
                exit 0
            else
                rm -f "$LOCK_F"
            fi
        fi
    }
    echo "$$" > "$LOCK_F"
fi

rotate_log
exec &> >(tee -a "$LOGFILE")

# shellcheck disable=SC2015
command -v "$BREW_BIN" >/dev/null 2>&1 && [[ -x "$BREW_BIN" ]] || { log "Fatal: Homebrew not found or not executable"; exit 1; }

export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_NO_INSTALL_CLEANUP=1
export HOMEBREW_NO_ANALYTICS=1
export HOMEBREW_NO_ENV_HINTS=1
export HOMEBREW_NO_INSTALL_UPGRADE=1

{
    log "brew update"
    LC_ALL=C "$BREW_BIN" update --quiet
    log "brew upgrade"
    LC_ALL=C "$BREW_BIN" upgrade --quiet
    log "brew upgrade --cask"
    LC_ALL=C "$BREW_BIN" upgrade --cask --quiet
    log "brew cu (cask upgrade)"
    LC_ALL=C "$BREW_BIN" cu -a -y --quiet
    log "brew cleanup"
    LC_ALL=C "$BREW_BIN" cleanup -s --prune=all --quiet
    log "Operation completed"
} || handle_error

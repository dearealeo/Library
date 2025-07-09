#!/bin/bash
set -euo pipefail
IFS=$'\n\t'
umask 077

readonly LOGFILE="$HOME/Library/Logs/uv_upgrade.log"
readonly LOCK_F="/tmp/uv_upgrade.lock"
readonly UV_BIN="/opt/homebrew/bin/uv"
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
            # shellcheck disable=SC2004
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

# shellcheck disable=SC2015
command -v "$UV_BIN" >/dev/null 2>&1 && [[ -x "$UV_BIN" ]] || { log "Fatal: $UV_BIN not found or not executable"; exit 1; }

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

upgrade_tools() {
    log "Listing installed tools"
    local tools
    tools=$(LC_ALL=C "$UV_BIN" tool list 2>/dev/null | awk '/^[^-[:space:]]/ {print $1}')
    tools=$(echo "$tools" | grep -vE '^\s*$|^-+$')
    [[ -z "$tools" ]] && { log "No tools installed, nothing to upgrade"; return 0; }

    log "Found installed tools, proceeding with upgrades"
    while IFS= read -r tool; do
        [[ -z "$tool" || "$tool" == "-" ]] && continue
        log "Upgrading: $tool"
        "$UV_BIN" tool upgrade "$tool" 2>/dev/null || log "Warning: Failed to upgrade $tool, continuing"
    done <<< "$tools"
}

upgrade_packages() {
    log "Checking for outdated Python packages"
    local outdated_packages
    outdated_packages=$("$UV_BIN" pip list --outdated 2>/dev/null | tail -n +3 | awk '{print $1}')
    [[ -z "$outdated_packages" ]] && { log "No outdated Python packages found"; return 0; }

    log "Found outdated Python packages, proceeding with upgrades"
    while IFS= read -r package; do
        [[ -z "$package" ]] && continue
        log "Upgrading Python package: $package"
        "$UV_BIN" pip install -U "$package" 2>/dev/null || log "Warning: Failed to upgrade package $package, continuing"
    done < <(printf '%s\n' "$outdated_packages")
}

{
    upgrade_tools
    upgrade_packages

    log "Operation completed"
} || handle_error

#!/bin/bash
set -euo pipefail
IFS=$'\n\t'
umask 077

readonly RIME_DIR="$HOME/Library/Rime"
readonly LOGFILE="$HOME/Library/Logs/rime_git_pull.log"
readonly LOCK_F="/tmp/rime_git_pull.lock"
readonly MAX_LOG_SIZE=1048576
readonly MAX_ROTATED_LOGS=1
readonly GIT_BIN="$(command -v git)"

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

[[ -d "$RIME_DIR/.git" ]] || { log "Fatal: $RIME_DIR is not a git repository"; exit 1; }
[[ -n "$GIT_BIN" ]] || { log "Fatal: git not found"; exit 1; }

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

{
    log "Executing git pull in $RIME_DIR"
    cd -- "$RIME_DIR" || { log "Failed to cd to $RIME_DIR"; exit 1; }
    export GIT_TERMINAL_PROMPT=0
    export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
    LC_ALL=C "$GIT_BIN" pull --quiet --ff-only
    log "Operation completed"
} || handle_error
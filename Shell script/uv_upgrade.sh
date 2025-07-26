#!/bin/zsh
emulate -L zsh
setopt err_exit no_unset pipe_fail warn_create_global local_options local_traps
setopt extended_glob numeric_glob_sort null_glob
umask 077

typeset -gr LOGFILE=$HOME/Library/Logs/uv_upgrade.log
typeset -gr LOCK_F=/tmp/uv_upgrade.lock
typeset -gr UV_BIN=/opt/homebrew/bin/uv
typeset -gir MAX_LOG_SIZE=1048576
typeset -gir MAX_ROTATED_LOGS=1
typeset -gi LOCK_FD

log() { printf '[%s] %s\n' ${(%):-%D{%Y-%m-%dT%H:%M:%S}} $* >&2 }

handle_error() {
    log "Error at ${funcfiletrace[1]}"
    (( LOCK_FD )) && exec {LOCK_FD}>&-
    [[ -f $LOCK_F ]] && rm -f $LOCK_F
    exit 1
}

rotate_log() {
    [[ ! -d ${LOGFILE:h} ]] && mkdir -p ${LOGFILE:h}
    [[ ! -f $LOGFILE ]] && return 0

    local -i size
    if (( $+commands[stat] )); then
        size=$(stat -f%z $LOGFILE 2>/dev/null || stat -c%s $LOGFILE 2>/dev/null || 0)
    else
        size=$(<$LOGFILE | wc -c)
    fi

    (( size < MAX_LOG_SIZE )) && return 0

    local -i i
    for (( i = MAX_ROTATED_LOGS; i >= 1; i-- )); do
        [[ $i -eq $MAX_ROTATED_LOGS && -f $LOGFILE.$i ]] && rm -f $LOGFILE.$i
        [[ -f $LOGFILE.$((i-1)) ]] && mv $LOGFILE.$((i-1)) $LOGFILE.$i
    done
    mv $LOGFILE $LOGFILE.1
}

acquire_lock() {
    if (( $+commands[flock] )); then
        exec {LOCK_FD}>$LOCK_F
        flock -n $LOCK_FD || exit 0
    else
        if [[ -f $LOCK_F ]]; then
            local -i pid=$(<$LOCK_F 2>/dev/null)
            if (( pid )) && kill -0 $pid 2>/dev/null; then
                exit 0
            fi
            rm -f $LOCK_F
        fi
        print $$ >$LOCK_F
    fi
}

cleanup() {
    (( LOCK_FD )) && exec {LOCK_FD}>&-
    [[ -f $LOCK_F ]] && rm -f $LOCK_F
}

upgrade_tools() {
    log "Listing installed tools"
    local -a tools_raw tools
    tools_raw=(${(f)"$($UV_BIN tool list 2>/dev/null)"})

    # Extract tool names using Zsh pattern matching
    for line in $tools_raw; do
        [[ $line =~ '^[^-[:space:]]+' ]] && tools+=(${line%% *})
    done

    (( ${#tools} == 0 )) && { log "No tools installed, nothing to upgrade"; return 0 }

    log "Found installed tools, proceeding with upgrades"
    local tool
    for tool in $tools; do
        [[ -z $tool || $tool == "-" ]] && continue
        log "Upgrading: $tool"
        $UV_BIN tool upgrade $tool 2>/dev/null || log "Warning: Failed to upgrade $tool, continuing"
    done
}

upgrade_packages() {
    log "Checking for outdated Python packages"
    local -a outdated_lines outdated_packages
    outdated_lines=(${(f)"$($UV_BIN pip list --outdated 2>/dev/null)"})

    # Skip header lines and extract package names
    outdated_packages=(${outdated_lines[3,-1]%% *})
    outdated_packages=(${outdated_packages:#""})

    (( ${#outdated_packages} == 0 )) && { log "No outdated Python packages found"; return 0 }

    log "Found outdated Python packages, proceeding with upgrades"
    local package
    for package in $outdated_packages; do
        [[ -z $package ]] && continue
        log "Upgrading Python package: $package"
        $UV_BIN pip install -U $package 2>/dev/null || log "Warning: Failed to upgrade package $package, continuing"
    done
}

trap handle_error ERR ZERR
trap cleanup EXIT INT TERM HUP

[[ ! -x $UV_BIN ]] && { log "Fatal: $UV_BIN not found or not executable"; exit 1 }

acquire_lock
rotate_log
exec > >(tee -a $LOGFILE) 2>&1

typeset -gx LC_ALL=C

{
    upgrade_tools
    upgrade_packages
    log "Operation completed"
} || handle_error

#!/bin/zsh
emulate -L zsh
setopt err_exit no_unset pipe_fail warn_create_global local_options local_traps
setopt extended_glob numeric_glob_sort null_glob multios auto_cd
setopt glob_dots glob_star_short hist_subst_pattern
umask 077

typeset -gr LOGFILE=$HOME/Library/Logs/uv_upgrade.log
typeset -gr LOCK_F=/tmp/uv_upgrade.lock
typeset -gr UV_BIN=/opt/homebrew/bin/uv
typeset -gir MAX_LOG_SIZE=1048576
typeset -gir MAX_ROTATED_LOGS=1
typeset -gi LOCK_FD

log() {
    local timestamp=${(%):-%D{%Y-%m-%dT%H:%M:%S}}
    print -r "[${timestamp}] $*" >&2
}

handle_error() {
    log "Error at ${funcfiletrace[1]}"
    (( LOCK_FD )) && exec {LOCK_FD}>&-
    [[ -f $LOCK_F ]] && rm -f $LOCK_F
    exit 1
}

rotate_log() {
    local logdir=${LOGFILE:h}
    [[ ! -d $logdir ]] && mkdir -p $logdir
    [[ ! -f $LOGFILE ]] && return 0

    local -i size
    if (( $+builtins[stat] )); then
        size=$(stat +size $LOGFILE 2>/dev/null || print 0)
    elif (( $+commands[stat] )); then
        local stat_output
        stat_output=$(stat -f%z $LOGFILE 2>/dev/null || stat -c%s $LOGFILE 2>/dev/null || print 0)
        size=${stat_output%% *}
    else
        size=${#$(<$LOGFILE)}
    fi

    (( size < MAX_LOG_SIZE )) && return 0

    local -i i
    for i in {$MAX_ROTATED_LOGS..1}; do
        local old_log="$LOGFILE.$((i-1))" new_log="$LOGFILE.$i"
        [[ $i -eq $MAX_ROTATED_LOGS && -f $new_log ]] && rm -f $new_log
        [[ -f $old_log ]] && mv $old_log $new_log
    done
    mv $LOGFILE $LOGFILE.1
}

acquire_lock() {
    if (( $+commands[flock] )); then
        exec {LOCK_FD}>$LOCK_F
        flock -n $LOCK_FD || exit 0
    else
        if [[ -f $LOCK_F ]]; then
            local -i pid
            { pid=$(<$LOCK_F) } 2>/dev/null
            if (( pid > 0 )) && kill -0 $pid 2>/dev/null; then
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
    local -a tools_raw tools uv_cmd

    if (( $+commands[uv] )); then
        uv_cmd=(uv)
    else
        uv_cmd=($UV_BIN)
    fi

    tools_raw=(${(f)"$($uv_cmd tool list 2>/dev/null)"})

    local line
    for line in $tools_raw; do
        [[ $line == [^-[:space:]]* ]] && tools+=(${line%% *})
    done

    (( ${#tools} == 0 )) && {
        log "No tools installed, nothing to upgrade"
        return 0
    }

    log "Found installed tools, proceeding with upgrades"
    local tool
    for tool in $tools; do
        [[ -z $tool || $tool == "-" ]] && continue
        log "Upgrading: $tool"
        $uv_cmd tool upgrade $tool 2>/dev/null || log "Warning: Failed to upgrade $tool, continuing"
    done
}

upgrade_packages() {
    log "Checking for outdated Python packages"
    local -a outdated_lines outdated_packages uv_cmd

    if (( $+commands[uv] )); then
        uv_cmd=(uv)
    else
        uv_cmd=($UV_BIN)
    fi

    outdated_lines=(${(f)"$($uv_cmd pip list --outdated 2>/dev/null)"})

    outdated_packages=(${outdated_lines[3,-1]%% *})
    outdated_packages=(${outdated_packages:#""})

    (( ${#outdated_packages} == 0 )) && {
        log "No outdated Python packages found"
        return 0
    }

    log "Found outdated Python packages, proceeding with upgrades"
    local package
    for package in $outdated_packages; do
        [[ -z $package ]] && continue
        log "Upgrading Python package: $package"
        $uv_cmd pip install -U $package 2>/dev/null || log "Warning: Failed to upgrade package $package, continuing"
    done
}

trap handle_error ERR ZERR
trap cleanup EXIT INT TERM HUP

(( ! $+commands[uv] )) && [[ ! -x $UV_BIN ]] && {
    log "Fatal: $UV_BIN not found or not executable"
    exit 1
}

acquire_lock
rotate_log
exec > >(tee -a $LOGFILE) 2>&1

typeset -gx LC_ALL=C

{
    upgrade_tools
    upgrade_packages
    log "Operation completed"
} || handle_error

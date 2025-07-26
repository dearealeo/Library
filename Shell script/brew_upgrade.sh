#!/bin/zsh
emulate -L zsh
setopt err_exit no_unset pipe_fail warn_create_global local_options local_traps
setopt extended_glob numeric_glob_sort null_glob
umask 077

typeset -gr LOGFILE=$HOME/Library/Logs/brew_upgrade.log
typeset -gr LOCK_F=/tmp/brew_upgrade.lock
typeset -gr BREW_BIN=/opt/homebrew/bin/brew
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

trap handle_error ERR ZERR
trap cleanup EXIT INT TERM HUP

[[ ! -x $BREW_BIN ]] && { log "Fatal: Homebrew not found or not executable"; exit 1 }

acquire_lock
rotate_log
exec > >(tee -a $LOGFILE) 2>&1

typeset -gx HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 \
           HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1 \
           HOMEBREW_NO_INSTALL_UPGRADE=1 LC_ALL=C

{
    log "brew update"
    $BREW_BIN update
    log "brew upgrade"
    $BREW_BIN upgrade
    log "brew upgrade --cask"
    $BREW_BIN upgrade --cask
    if $BREW_BIN tap | grep -q '^buo/cask-upgrade$'; then
        log "brew cu (cask upgrade)"
        $BREW_BIN cu -a -y
    fi
    log "brew cleanup"
    $BREW_BIN cleanup -s --prune=all
    log "Operation completed"
} || handle_error
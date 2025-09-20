#!/bin/zsh
emulate -L zsh
setopt err_exit no_unset pipe_fail warn_create_global local_options local_traps
setopt extended_glob numeric_glob_sort null_glob multios auto_cd
setopt glob_dots glob_star_short hist_subst_pattern
umask 077

typeset -gr LOGFILE=$HOME/Library/Logs/brew_upgrade.log
typeset -gr LOCK_F=/tmp/brew_upgrade.lock
typeset -gr BREW_BIN=/opt/homebrew/bin/brew
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

trap handle_error ERR ZERR
trap cleanup EXIT INT TERM HUP

(( ! $+commands[brew] )) && [[ ! -x $BREW_BIN ]] && {
    log "Fatal: Homebrew not found or not executable"
    exit 1
}

acquire_lock
rotate_log
exec > >(tee -a $LOGFILE) 2>&1

typeset -gx HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 \
           HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1 \
           HOMEBREW_NO_INSTALL_UPGRADE=1 LC_ALL=C

{
    local -a brew_cmd
    if (( $+commands[brew] )); then
        brew_cmd=(brew)
    else
        brew_cmd=($BREW_BIN)
    fi

    log "brew update"
    $brew_cmd update

    log "brew upgrade"
    $brew_cmd upgrade

    log "brew upgrade --cask"
    $brew_cmd upgrade --cask

    local -a taps
    taps=(${(f)"$($brew_cmd tap 2>/dev/null)"})
    if (( ${taps[(I)buo/cask-upgrade]} )); then
        log "brew cu (cask upgrade)"
        $brew_cmd cu -a -y
    fi

    log "brew cleanup"
    $brew_cmd cleanup -s --prune=all

    log "Operation completed"
} || handle_error
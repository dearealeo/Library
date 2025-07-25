#!/bin/zsh
emulate -L zsh
setopt err_exit no_unset pipe_fail warn_create_global local_options local_traps
setopt extended_glob numeric_glob_sort null_glob
umask 077

typeset -gr RIME_DIR=$HOME/Library/Rime
typeset -gr LOGFILE=$HOME/Library/Logs/rime_git_pull.log
typeset -gr LOCK_F=/tmp/rime_git_pull.lock
typeset -gir MAX_LOG_SIZE=1048576
typeset -gir MAX_ROTATED_LOGS=1
typeset -gr GIT_BIN=$commands[git]
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

[[ ! -d $RIME_DIR/.git ]] && { log "Fatal: $RIME_DIR is not a git repository"; exit 1 }
[[ -z $GIT_BIN ]] && { log "Fatal: git not found"; exit 1 }

acquire_lock
rotate_log
exec > >(tee -a $LOGFILE) 2>&1

{
    log "Executing git pull in $RIME_DIR"
    cd $RIME_DIR || { log "Failed to cd to $RIME_DIR"; exit 1 }

    typeset -gx GIT_TERMINAL_PROMPT=0 \
               GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
               LC_ALL=C

    $GIT_BIN pull
    log "Operation completed"
} || handle_error

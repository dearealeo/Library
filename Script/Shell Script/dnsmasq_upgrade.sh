#!/bin/zsh
emulate -L zsh
setopt err_exit no_unset pipe_fail warn_create_global local_options local_traps
setopt extended_glob numeric_glob_sort null_glob multios auto_cd
setopt glob_dots glob_star_short hist_subst_pattern
umask 077

typeset -gr WORKDIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/SmartDNS"
typeset -gr REPO_DIR="$WORKDIR/dnsmasq-china-list"
typeset -gr LOGFILE="$HOME/Library/Logs/dnsmasq_upgrade.log"
typeset -gr LOCK_F="/tmp/dnsmasq_upgrade.lock"
typeset -gir MAX_LOG_SIZE=1048576
typeset -gir MAX_ROTATED_LOGS=1
typeset -gr GIT_BIN=$commands[git]
typeset -gr MAKE_BIN=$commands[make]
typeset -gi LOCK_FD

log() {
    local timestamp=${(%):-%D{%Y-%m-%dT%H:%M:%S}}
    print -r "[${timestamp}] $*" >&2
}

handle_error() {
    log "Error at ${funcfiletrace[1]}"
    (( LOCK_FD )) && exec {LOCK_FD}>&-
    [[ -f $LOCK_F ]] && rm -f $LOCK_F
    [[ -d $REPO_DIR ]] && { cd $WORKDIR; rm -rf $REPO_DIR }
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

(( ! $+commands[git] )) && [[ -z $GIT_BIN ]] && {
    log "Fatal: git not found"
    exit 1
}
(( ! $+commands[make] )) && [[ -z $MAKE_BIN ]] && {
    log "Fatal: make not found"
    exit 1
}

acquire_lock
rotate_log
exec > >(tee -a $LOGFILE) 2>&1

typeset -gx LC_ALL=C

{
    [[ ! -d $WORKDIR ]] && mkdir -p $WORKDIR

    local -a git_cmd make_cmd
    if (( $+commands[git] )); then
        git_cmd=(git)
    else
        git_cmd=($GIT_BIN)
    fi

    if (( $+commands[make] )); then
        make_cmd=(make)
    else
        make_cmd=($MAKE_BIN)
    fi

    if [[ -d $REPO_DIR/.git ]]; then
        log "Updating dnsmasq-china-list repo"
        $git_cmd -C $REPO_DIR pull --ff-only
    else
        log "Cloning dnsmasq-china-list repo"
        $git_cmd clone --depth 1 https://github.com/felixonmars/dnsmasq-china-list.git $REPO_DIR
    fi

    builtin cd $REPO_DIR

    log "Generating smartdns domain rules"
    $make_cmd SERVER=domestic SMARTDNS_SPEEDTEST_MODE=tcp:80 smartdns-domain-rules

    log "Copying generated files to $WORKDIR"
    local -a config_files
    config_files=(accelerated-domains.china.domain.smartdns.conf apple.china.domain.smartdns.conf)

    local file
    for file in $config_files; do
        [[ -f $file ]] && cp $file $WORKDIR/
    done

    log "Cleaning up repo directory"
    builtin cd $WORKDIR
    rm -rf $REPO_DIR

    log "Operation completed"
} || handle_error

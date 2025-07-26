#!/bin/zsh
emulate -L zsh
setopt err_exit no_unset pipe_fail warn_create_global local_options local_traps
setopt extended_glob numeric_glob_sort null_glob multios
umask 077

typeset -gr WORKDIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/SmartDNS"
typeset -gr LOGFILE="$HOME/Library/Logs/local-dns-mapping_upgrade.log"
typeset -gr LOCK_F="/tmp/local-dns-mapping_upgrade.lock"
typeset -gir MAX_LOG_SIZE=1048576
typeset -gir MAX_ROTATED_LOGS=1
typeset -gir MAX_CONCURRENT=8
typeset -gir TIMEOUT=10
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
        size=$(stat +size $LOGFILE 2>/dev/null || 0)
    elif (( $+commands[stat] )); then
        size=$(stat -f%z $LOGFILE 2>/dev/null || stat -c%s $LOGFILE 2>/dev/null || 0)
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

fetch_ruleset() {
    local url=$1 file=$2
    local -a curl_opts
    
    curl_opts=(
        --fail --silent --show-error --location
        --retry 3 --retry-delay 1 --retry-max-time 30
        --connect-timeout $TIMEOUT --max-time $((TIMEOUT * 3))
        --compressed --tcp-nodelay
        --user-agent "SmartDNS/1.0"
        --output "$file.tmp"
        "$url"
    )
    
    if $commands[curl] $curl_opts; then
        mv "$file.tmp" "$file" && return 0
    fi
    
    [[ -f "$file.tmp" ]] && rm -f "$file.tmp"
    return 1
}

download() {
    local -A rulesets=(
        "https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/alibaba.conf"   "alibaba.conf"
        "https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/tencent.conf"   "tencent.conf"
        "https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/bilibili.conf"  "bilibili.conf"
        "https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/xiaomi.conf"    "xiaomi.conf"
        "https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/bytedance.conf" "bytedance.conf"
        "https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/baidu.conf"     "baidu.conf"
        "https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/qihoo360.conf"  "qihoo360.conf"
        "https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/router.conf"    "router.conf"
        "https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/lan_without_real_ip.conf" "lan_without_real_ip.conf"
        "https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/lan_with_realip.conf"     "lan_with_realip.conf"
    )
    
    local -A active_pids pid_to_url pid_to_file
    local -a pending_urls pending_files completed_pids
    local -i completed=0 failed=0
    
    pending_urls=(${(k)rulesets})
    pending_files=(${(v)rulesets})
    
    local -i total=${#pending_urls}
    
    while (( ${#pending_urls} > 0 || ${#active_pids} > 0 )); do
        
        while (( ${#pending_urls} > 0 && ${#active_pids} < MAX_CONCURRENT )); do
            local url=$pending_urls[1] filename=$pending_files[1]
            local filepath="$WORKDIR/$filename"
            
            pending_urls[1]=()
            pending_files[1]=()
            
            log "Starting download: $filename"
            { fetch_ruleset "$url" "$filepath" } &
            local pid=$!
            
            active_pids[$pid]=1
            pid_to_url[$pid]=$url
            pid_to_file[$pid]=$filename
        done
        
        completed_pids=()
        for pid in ${(k)active_pids}; do
            if ! kill -0 $pid 2>/dev/null; then
                completed_pids+=($pid)
            fi
        done
        
        if (( ${#completed_pids} > 0 )); then
            for pid in $completed_pids; do
                wait $pid
                local -i exit_code=$?
                local filename=$pid_to_file[$pid]
                
                unset "active_pids[$pid]" "pid_to_url[$pid]" "pid_to_file[$pid]"
                
                if (( exit_code == 0 )); then
                    log "Completed: $filename"
                    (( completed++ ))
                else
                    log "Failed: $filename"
                    (( failed++ ))
                fi
            done
        else
            read -t 0.1 -k 1 </dev/null 2>/dev/null || true
        fi
    done
    
    log "Download summary: $completed completed, $failed failed"
    return $(( failed > 0 ? 1 : 0 ))
}

trap handle_error ERR ZERR
trap cleanup EXIT INT TERM HUP

(( ! $+commands[curl] )) && { 
    log "Fatal: curl not found or not executable" 
    exit 1 
}

acquire_lock
rotate_log
exec > >(tee -a $LOGFILE) 2>&1

typeset -gx LC_ALL=C

{
    [[ ! -d $WORKDIR ]] && mkdir -p $WORKDIR
    
    log "Starting concurrent ruleset downloads"
    download
    
    log "All RULE-SETs processed"
} || handle_error

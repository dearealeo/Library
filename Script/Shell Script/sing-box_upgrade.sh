#!/bin/zsh
emulate -L zsh
setopt err_exit no_unset pipe_fail warn_create_global local_options local_traps
setopt extended_glob numeric_glob_sort null_glob multios auto_cd
setopt glob_dots glob_star_short hist_subst_pattern
umask 077

typeset -gr TARGET_DIR="/Users/dearealeo/Library/Mobile Documents/iCloud~io~nekohasekai~sfavt/Documents/rule_set"
typeset -gr LOGFILE="$HOME/Library/Logs/sing-box_upgrade.log"
typeset -gr LOCK_F="/tmp/sing-box_upgrade.lock"
typeset -gir MAX_LOG_SIZE=1048576
typeset -gir MAX_ROTATED_LOGS=1
typeset -gr CURL_BIN=$commands[curl]
typeset -gi LOCK_FD

typeset -grA URL_TO_TAG=(
    "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ads-all.srs" "geosite-category-ads-all"
    "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-geolocation-!cn.srs" "geosite-geolocation-!cn"
    "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs" "geosite-cn"
    "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-cn.srs" "geoip-cn"
    "https://ruleset.skk.moe/sing-box/domainset/reject.json" "reject.domainset"
    "https://ruleset.skk.moe/sing-box/domainset/reject_extra.json" "reject-extra.domainset"
    "https://ruleset.skk.moe/sing-box/domainset/reject_phishing.json" "reject-phishing.domainset"
    "https://ruleset.skk.moe/sing-box/domainset/speedtest.json" "speedtest.domainset"
    "https://ruleset.skk.moe/sing-box/domainset/apple_cdn.json" "apple-cdn.domainset"
    "https://ruleset.skk.moe/sing-box/domainset/cdn.json" "cdn.domainset"
    "https://ruleset.skk.moe/sing-box/domainset/download.json" "download.domainset"
    "https://ruleset.skk.moe/sing-box/domainset/game-download.json" "game-download.domainset"
    "https://ruleset.skk.moe/sing-box/domainset/icloud_private_relay.json" "icloud-private-relay.domainset"
    "https://raw.githubusercontent.com/privacy-protection-tools/anti-ad.github.io/master/docs/anti-ad-sing-box.srs" "anti-ad"
    "https://ruleset.skk.moe/sing-box/non_ip/stream_us.json" "stream-us.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/stream_jp.json" "stream-jp.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/stream_hk.json" "stream-hk.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/stream_tw.json" "stream-tw.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/stream_kr.json" "stream-kr.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/stream_eu.json" "stream-eu.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/stream_biliintl.json" "stream-biliintl.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/stream.json" "stream.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/ai.json" "ai.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/telegram.json" "telegram.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/apple_cdn.json" "apple-cdn.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/apple_services.json" "apple-services.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/apple_cn.json" "apple-cn.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/microsoft_cdn.json" "microsoft-cdn.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/microsoft.json" "microsoft.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/neteasemusic.json" "neteasemusic.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/download.json" "download.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/lan.json" "lan.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/direct.json" "direct.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/domestic.json" "domestic.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/global.json" "global.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/reject.json" "reject.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/reject-drop.json" "reject-drop.non_ip"
    "https://ruleset.skk.moe/sing-box/non_ip/reject-no-drop.json" "reject-no-drop.non_ip"
    "https://ruleset.skk.moe/sing-box/ip/lan.json" "lan.ip"
    "https://ruleset.skk.moe/sing-box/ip/telegram.json" "telegram.ip"
    "https://ruleset.skk.moe/sing-box/ip/telegram_asn.json" "telegram-asn.ip"
    "https://ruleset.skk.moe/sing-box/ip/apple_services.json" "apple-services.ip"
    "https://ruleset.skk.moe/sing-box/ip/cdn.json" "cdn.ip"
    "https://ruleset.skk.moe/sing-box/ip/china_ip.json" "china-ip.ip"
    "https://ruleset.skk.moe/sing-box/ip/china_ip_ipv6.json" "china-ip-ipv6.ip"
    "https://ruleset.skk.moe/sing-box/ip/domestic.json" "domestic.ip"
)

typeset -gra URLS=(${(k)URL_TO_TAG})

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

download_files() {
    local -a curl_cmd
    if (( $+commands[curl] )); then
        curl_cmd=(curl)
    else
        curl_cmd=($CURL_BIN)
    fi

    log "Creating target directory: $TARGET_DIR"
    [[ ! -d $TARGET_DIR ]] && mkdir -p $TARGET_DIR

    local url filename tag_name file_extension
    local -i total=${#URLS} current=0 success=0 failed=0

    for url in $URLS; do
        (( current++ ))
        tag_name=$URL_TO_TAG[$url]

        if [[ $url == *.srs ]]; then
            file_extension="srs"
        elif [[ $url == *.json ]]; then
            file_extension="json"
        else
            log "Unsupported file type: $url"
            (( failed++ ))
            continue
        fi

        filename="${tag_name}.${file_extension}"

        log "[$current/$total] Downloading: $filename (from $url)"

        if $curl_cmd -fsSL --connect-timeout 30 --max-time 300 -o "$TARGET_DIR/$filename" "$url"; then
            log "Downloaded: $filename"
            (( success++ ))
        else
            log "Failed to download: $filename from $url"
            (( failed++ ))
        fi
    done

    log "Download summary: $success successful, $failed failed out of $total total files"

    return $(( failed > 0 ? 1 : 0 ))
}

trap handle_error ERR ZERR
trap cleanup EXIT INT TERM HUP

(( ! $+commands[curl] )) && [[ -z $CURL_BIN ]] && {
    log "Fatal: curl not found"
    exit 1
}

acquire_lock
rotate_log
exec > >(tee -a $LOGFILE) 2>&1

typeset -gx LC_ALL=C

{
    download_files
    log "Operation completed"
} || handle_error

#!/usr/bin/env bash
set -eEuo pipefail
shopt -s inherit_errexit extglob globstar nullglob dotglob
umask 077

readonly TARGET_DIR="$HOME/Library/Mobile Documents/iCloud~io~nekohasekai~sfavt/Documents/rule_set"
readonly LOGFILE="$HOME/Library/Logs/sing-box_upgrade.log"
readonly LOCK_F=/tmp/sing-box_upgrade.lock
declare -ir MAX_LOG_SIZE=1048576 MAX_ROTATED_LOGS=1
declare -i LOCK_FD=0

declare -A URL_TO_TAG=(
	["https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ads-all.srs"]="geosite-category-ads-all"
	["https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-geolocation-!cn.srs"]="geosite-geolocation-!cn"
	["https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs"]="geosite-cn"
	["https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-cn.srs"]="geoip-cn"
	["https://ruleset.skk.moe/sing-box/domainset/reject.json"]="reject.domainset"
	["https://ruleset.skk.moe/sing-box/domainset/reject_extra.json"]="reject-extra.domainset"
	["https://ruleset.skk.moe/sing-box/domainset/reject_phishing.json"]="reject-phishing.domainset"
	["https://ruleset.skk.moe/sing-box/domainset/speedtest.json"]="speedtest.domainset"
	["https://ruleset.skk.moe/sing-box/domainset/apple_cdn.json"]="apple-cdn.domainset"
	["https://ruleset.skk.moe/sing-box/domainset/cdn.json"]="cdn.domainset"
	["https://ruleset.skk.moe/sing-box/domainset/download.json"]="download.domainset"
	["https://ruleset.skk.moe/sing-box/domainset/game-download.json"]="game-download.domainset"
	["https://ruleset.skk.moe/sing-box/domainset/icloud_private_relay.json"]="icloud-private-relay.domainset"
	["https://raw.githubusercontent.com/privacy-protection-tools/anti-ad.github.io/master/docs/anti-ad-sing-box.srs"]="anti-ad"
	["https://ruleset.skk.moe/sing-box/non_ip/stream_us.json"]="stream-us.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/stream_jp.json"]="stream-jp.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/stream_hk.json"]="stream-hk.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/stream_tw.json"]="stream-tw.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/stream_kr.json"]="stream-kr.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/stream_eu.json"]="stream-eu.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/stream_biliintl.json"]="stream-biliintl.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/stream.json"]="stream.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/ai.json"]="ai.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/telegram.json"]="telegram.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/apple_cdn.json"]="apple-cdn.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/apple_services.json"]="apple-services.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/apple_cn.json"]="apple-cn.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/microsoft_cdn.json"]="microsoft-cdn.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/microsoft.json"]="microsoft.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/neteasemusic.json"]="neteasemusic.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/download.json"]="download.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/lan.json"]="lan.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/direct.json"]="direct.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/domestic.json"]="domestic.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/global.json"]="global.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/reject.json"]="reject.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/reject-drop.json"]="reject-drop.non_ip"
	["https://ruleset.skk.moe/sing-box/non_ip/reject-no-drop.json"]="reject-no-drop.non_ip"
	["https://ruleset.skk.moe/sing-box/ip/lan.json"]="lan.ip"
	["https://ruleset.skk.moe/sing-box/ip/telegram.json"]="telegram.ip"
	["https://ruleset.skk.moe/sing-box/ip/telegram_asn.json"]="telegram-asn.ip"
	["https://ruleset.skk.moe/sing-box/ip/apple_services.json"]="apple-services.ip"
	["https://ruleset.skk.moe/sing-box/ip/cdn.json"]="cdn.ip"
	["https://ruleset.skk.moe/sing-box/ip/china_ip.json"]="china-ip.ip"
	["https://ruleset.skk.moe/sing-box/ip/china_ip_ipv6.json"]="china-ip-ipv6.ip"
	["https://ruleset.skk.moe/sing-box/ip/domestic.json"]="domestic.ip"
)

log() {
	printf -v ts '%(%Y-%m-%dT%H:%M:%S)T' -1
	printf '[%s] %s\n' "$ts" "$*" >&2
}

error() {
	log "Error at ${BASH_SOURCE[1]##*/}:${BASH_LINENO[0]}:${FUNCNAME[1]}"
	((LOCK_FD)) && exec {LOCK_FD}>&-
	[[ -f $LOCK_F ]] && rm -f -- "$LOCK_F"
	exit 1
}

rotate() {
	local logdir="${LOGFILE%/*}"
	[[ -d $logdir ]] || mkdir -p -- "$logdir"
	[[ -f $LOGFILE ]] || return 0
	local -i size
	if [[ -r /proc/self/fd/0 ]]; then
		size=$(<"$LOGFILE" wc -c)
	elif stat --version &>/dev/null; then
		size=$(stat -c%s -- "$LOGFILE" 2>/dev/null || printf 0)
	else
		size=$(stat -f%z -- "$LOGFILE" 2>/dev/null || printf 0)
	fi
	((size < MAX_LOG_SIZE)) && return 0
	local -i i
	for ((i = MAX_ROTATED_LOGS; i > 0; i--)); do
		local old_log="$LOGFILE.$((i - 1))" new_log="$LOGFILE.$i"
		[[ $i -eq $MAX_ROTATED_LOGS && -f $new_log ]] && rm -f -- "$new_log"
		[[ -f $old_log ]] && mv -f -- "$old_log" "$new_log"
	done
	mv -f -- "$LOGFILE" "$LOGFILE.1"
}

lock() {
	if command -v flock &>/dev/null; then
		exec {LOCK_FD}>"$LOCK_F"
		flock -n $LOCK_FD || exit 0
	else
		if [[ -f $LOCK_F ]]; then
			local -i pid
			{ read -r pid <"$LOCK_F"; } 2>/dev/null || pid=0
			((pid > 0)) && kill -0 $pid 2>/dev/null && exit 0
			rm -f -- "$LOCK_F"
		fi
		printf '%d' $$ >"$LOCK_F"
	fi
}

cleanup() {
	((LOCK_FD)) && exec {LOCK_FD}>&-
	[[ -f $LOCK_F ]] && rm -f -- "$LOCK_F"
}

download() {
	log "Creating target directory: $TARGET_DIR"
	[[ -d $TARGET_DIR ]] || mkdir -p -- "$TARGET_DIR"
	local -i total=${#URL_TO_TAG[@]} current=0 success=0 failed=0
	local url tag_name file_extension filename
	for url in "${!URL_TO_TAG[@]}"; do
		((current++))
		tag_name="${URL_TO_TAG[$url]}"
		case $url in
		*.srs)
			file_extension=srs
			;;
		*.json)
			file_extension=json
			;;
		*)
			log "Unsupported file type: $url"
			((failed++))
			continue
			;;
		esac
		filename="$tag_name.$file_extension"
		log "[$current/$total] Downloading: $filename (from $url)"
		if curl -fsSL --connect-timeout 30 --max-time 300 -o "$TARGET_DIR/$filename" "$url"; then
			log "Downloaded: $filename"
			((success++))
		else
			log "Failed to download: $filename from $url"
			((failed++))
		fi
	done
	log "Download: $success successful, $failed failed out of $total total files"
	return $((failed > 0 ? 1 : 0))
}

trap error ERR
trap cleanup EXIT INT TERM HUP

command -v curl &>/dev/null || {
	log "Fatal: curl not found"
	exit 1
}

lock
rotate
exec > >(exec tee -a "$LOGFILE") 2>&1
export LC_ALL=C
{
	download
	log "Operation completed"
} || error

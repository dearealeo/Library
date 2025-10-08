#!/usr/bin/env bash
set -eEuo pipefail
shopt -s inherit_errexit extglob globstar nullglob dotglob
umask 077

readonly WORKDIR=$HOME/Library/Mobile\ Documents/com~apple~CloudDocs/SmartDNS
readonly LOGFILE=$HOME/Library/Logs/smartdns_upgrade.log
readonly LOCK_F=/tmp/smartdns_upgrade.lock
declare -ir MAX_LOG_SIZE=1048576 MAX_ROTATED_LOGS=1 MAX_CONCURRENT=8 TIMEOUT=10
declare -i LOCK_FD=0
declare -A RULESETS=(
	["https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/alibaba.conf"]=alibaba.conf
	["https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/tencent.conf"]=tencent.conf
	["https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/bilibili.conf"]=bilibili.conf
	["https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/xiaomi.conf"]=xiaomi.conf
	["https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/bytedance.conf"]=bytedance.conf
	["https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/baidu.conf"]=baidu.conf
	["https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/qihoo360.conf"]=qihoo360.conf
	["https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/router.conf"]=router.conf
	["https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/lan_without_real_ip.conf"]=lan_without_real_ip.conf
	["https://ruleset.skk.moe/Modules/Rules/sukka_local_dns_mapping/lan_with_realip.conf"]=lan_with_realip.conf
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
	local logdir=${LOGFILE%/*}
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
		local old_log=$LOGFILE.$((i - 1)) new_log=$LOGFILE.$i
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
		printf '%d' $ >"$LOCK_F"
	fi
}

cleanup() {
	((LOCK_FD)) && exec {LOCK_FD}>&-
	[[ -f $LOCK_F ]] && rm -f -- "$LOCK_F"
}

fetch_ruleset() {
	local url=$1 file=$2
	curl --fail --silent --show-error --location --retry 3 --retry-delay 1 --retry-max-time 30 \
		--connect-timeout $TIMEOUT --max-time $((TIMEOUT * 3)) --compressed --tcp-nodelay --http2 \
		--user-agent SmartDNS/1.0 -o "$file.tmp" "$url" || {
		[[ -f $file.tmp ]] && rm -f -- "$file.tmp"
		return 1
	}
	mv -f -- "$file.tmp" "$file" || return 1
	local smartdns_file=${file%.conf}.smartdns.conf
	local -a domains=()
	local line domain
	while IFS= read -r line; do
		[[ $line == @(DOMAIN|DOMAIN-SUFFIX),* ]] || continue
		domain=${line#*,}
		domain=${domain## }
		domain=${domain%% }
		[[ -n $domain && $domain != *.skk.moe ]] || continue
		domains+=("$domain")
	done <"$file"
	((${#domains[@]} > 0)) && printf '%s\n' "${domains[@]}" >>"$smartdns_file"
	rm -f -- "$file"
	return 0
}

fetch_anti_ad() {
	local url=https://raw.githubusercontent.com/privacy-protection-tools/anti-AD/master/anti-ad-smartdns.conf
	local file=$WORKDIR/anti-ad-smartdns.conf
	log "Downloading anti-AD smartdns configuration"
	if curl --fail --silent --show-error --location --retry 3 --retry-delay 1 --retry-max-time 30 \
		--connect-timeout $TIMEOUT --max-time $((TIMEOUT * 3)) --compressed --tcp-nodelay --http2 \
		--user-agent SmartDNS/1.0 -o "$file.tmp" "$url"; then
		mv -f -- "$file.tmp" "$file"
		log "Successfully downloaded anti-ad-smartdns.conf"
		return 0
	fi
	[[ -f $file.tmp ]] && rm -f -- "$file.tmp"
	log "Failed to download anti-ad-smartdns.conf"
	return 1
}

download() {
	declare -A active_pids=() pid_to_file=()
	local -a pending_urls=("${!RULESETS[@]}") pending_files=("${RULESETS[@]}")
	declare -i completed=0 failed=0
	while ((${#pending_urls[@]} > 0 || ${#active_pids[@]} > 0)); do
		while ((${#pending_urls[@]} > 0 && ${#active_pids[@]} < MAX_CONCURRENT)); do
			local url=${pending_urls[0]} filename=${pending_files[0]}
			local filepath=$WORKDIR/$filename
			unset 'pending_urls[0]' 'pending_files[0]'
			pending_urls=("${pending_urls[@]}")
			pending_files=("${pending_files[@]}")
			log "Starting download: $filename"
			fetch_ruleset "$url" "$filepath" &
			local pid=$!
			active_pids[$pid]=1
			pid_to_file[$pid]=$filename
		done
		local -a finished_pids=()
		for pid in "${!active_pids[@]}"; do
			kill -0 "$pid" 2>/dev/null || finished_pids+=("$pid")
		done
		for pid in "${finished_pids[@]}"; do
			[[ -n ${pid_to_file[$pid]:-} ]] || continue
			wait "$pid"
			local -i exit_code=$?
			local filename=${pid_to_file[$pid]}
			if ((exit_code == 0)); then
				log "Completed: $filename"
				((completed++))
			else
				log "Failed: $filename"
				((failed++))
			fi
			unset 'active_pids[$pid]' 'pid_to_url[$pid]' 'pid_to_file[$pid]'
		done
	done
	log "Download summary: $completed completed, $failed failed"
	return $((failed > 0 ? 1 : 0))
}

trap error ERR
trap cleanup EXIT INT TERM HUP

command -v curl &>/dev/null || {
	log "Fatal: curl not found or not executable"
	exit 1
}

lock
rotate
exec > >(exec tee -a "$LOGFILE") 2>&1
export LC_ALL=C

{
	[[ -d $WORKDIR ]] || mkdir -p -- "$WORKDIR"
	log "Starting concurrent ruleset downloads"
	download
	log "All RULE-SETs processed"
	log "Downloading anti-AD configuration"
	fetch_anti_ad
	log "Anti-AD download completed"
} || error

#!/usr/bin/env bash
set -eEuo pipefail
shopt -s inherit_errexit extglob globstar nullglob dotglob
umask 077

readonly WORKDIR=$HOME/Library/Mobile\ Documents/com~apple~CloudDocs/SmartDNS
readonly REPO_DIR=$WORKDIR/dnsmasq-china-list
readonly LOGFILE=$HOME/Library/Logs/dnsmasq_upgrade.log
readonly LOCK_F=/tmp/dnsmasq_upgrade.lock
declare -ir MAX_LOG_SIZE=1048576 MAX_ROTATED_LOGS=1
declare -i LOCK_FD=0

log() {
	printf -v ts '%(%Y-%m-%dT%H:%M:%S)T' -1
	printf '[%s] %s\n' "$ts" "$*" >&2
}

error() {
	log "Error at ${BASH_SOURCE[1]##*/}:${BASH_LINENO[0]}:${FUNCNAME[1]}"
	((LOCK_FD)) && exec {LOCK_FD}>&-
	[[ -f $LOCK_F ]] && rm -f -- "$LOCK_F"
	[[ -d $REPO_DIR ]] && { cd "$WORKDIR" && rm -rf -- "$REPO_DIR"; }
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
		printf '%d' $$ >"$LOCK_F"
	fi
}

cleanup() {
	((LOCK_FD)) && exec {LOCK_FD}>&-
	[[ -f $LOCK_F ]] && rm -f -- "$LOCK_F"
}

trap error ERR
trap cleanup EXIT INT TERM HUP

command -v git &>/dev/null || {
	log "Fatal: git not found"
	exit 1
}

command -v make &>/dev/null || {
	log "Fatal: make not found"
	exit 1
}

lock
rotate
exec > >(exec tee -a "$LOGFILE") 2>&1
export LC_ALL=C

{
	[[ -d $WORKDIR ]] || mkdir -p -- "$WORKDIR"
	if [[ -d $REPO_DIR/.git ]]; then
		log "Updating dnsmasq-china-list repo"
		git -C "$REPO_DIR" pull --ff-only
	else
		log "Cloning dnsmasq-china-list repo"
		git clone --depth 1 https://github.com/felixonmars/dnsmasq-china-list.git "$REPO_DIR"
	fi
	cd "$REPO_DIR" || exit 1
	log "Generating smartdns domain rules"
	make SERVER=domestic SMARTDNS_SPEEDTEST_MODE=tcp:80 smartdns-domain-rules
	log "Copying generated files to $WORKDIR"
	declare -a config_files=(accelerated-domains.china.domain.smartdns.conf apple.china.domain.smartdns.conf)
	declare file
	for file in "${config_files[@]}"; do
		[[ -f $file ]] && cp -f -- "$file" "$WORKDIR/"
	done
	log "Cleaning up repo directory"
	cd "$WORKDIR" || exit 1
	rm -rf -- "$REPO_DIR"
	log "Operation completed"
} || error

#!/usr/bin/env bash
set -eEuo pipefail
shopt -s inherit_errexit extglob globstar nullglob dotglob
umask 077

readonly LOGFILE=$HOME/Library/Logs/brew_upgrade.log
readonly LOCK_F=/tmp/brew_upgrade.lock
readonly BREW_BIN=/opt/homebrew/bin/brew
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

command -v brew &>/dev/null || [[ -x $BREW_BIN ]] || {
	log "Fatal: Homebrew not found or not executable"
	exit 1
}

lock
rotate
exec > >(exec tee -a "$LOGFILE") 2>&1

export HOMEBREW_NO_AUTO_UPDATE=1 \
	HOMEBREW_NO_INSTALL_CLEANUP=1 \
	HOMEBREW_NO_ANALYTICS=1 \
	HOMEBREW_NO_ENV_HINTS=1 \
	HOMEBREW_NO_INSTALL_UPGRADE=1 \
	LC_ALL=C

{
	declare -a brew_cmd
	if command -v brew &>/dev/null; then
		brew_cmd=(brew)
	else
		brew_cmd=("$BREW_BIN")
	fi

	log "brew update"
	"${brew_cmd[@]}" update

	log "brew upgrade"
	"${brew_cmd[@]}" upgrade

	log "brew upgrade --cask"
	"${brew_cmd[@]}" upgrade --cask

	mapfile -t taps < <("${brew_cmd[@]}" tap 2>/dev/null || :)
	declare tap
	for tap in "${taps[@]}"; do
		[[ $tap == buo/cask-upgrade ]] && {
			log "brew cu (cask upgrade)"
			"${brew_cmd[@]}" cu -a -y
			break
		}
	done

	log "brew cleanup"
	"${brew_cmd[@]}" cleanup -s --prune=all

	log "Operation completed"
} || error

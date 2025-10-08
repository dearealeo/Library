#!/usr/bin/env bash
set -eEuo pipefail
shopt -s inherit_errexit extglob globstar nullglob dotglob
umask 077

readonly LOGFILE=$HOME/Library/Logs/uv_upgrade.log
readonly LOCK_F=/tmp/uv_upgrade.lock
readonly UV_BIN=/opt/homebrew/bin/uv
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

upgrade_tools() {
	log "Listing installed tools"
	local -a tools_raw uv_cmd
	if command -v uv &>/dev/null; then
		uv_cmd=(uv)
	else
		uv_cmd=("$UV_BIN")
	fi
	mapfile -t tools_raw < <("${uv_cmd[@]}" tool list 2>/dev/null || :)
	local -a tools=()
	local line
	for line in "${tools_raw[@]}"; do
		[[ $line == [^-[:space:]]* ]] && tools+=("${line%% *}")
	done
	((${#tools[@]} == 0)) && {
		log "No tools installed, nothing to upgrade"
		return 0
	}
	log "Found installed tools, proceeding with upgrades"
	local tool
	for tool in "${tools[@]}"; do
		[[ -n $tool && $tool != - ]] || continue
		log "Upgrading: $tool"
		"${uv_cmd[@]}" tool upgrade "$tool" 2>/dev/null || log "Warning: Failed to upgrade $tool, continuing"
	done
}

upgrade_packages() {
	log "Checking for outdated Python packages"
	local -a outdated_lines uv_cmd
	if command -v uv &>/dev/null; then
		uv_cmd=(uv)
	else
		uv_cmd=("$UV_BIN")
	fi
	mapfile -t outdated_lines < <("${uv_cmd[@]}" pip list --outdated 2>/dev/null || :)
	((${#outdated_lines[@]} < 3)) && {
		log "No outdated Python packages found"
		return 0
	}
	local -a outdated_packages=()
	local -i i
	for ((i = 2; i < ${#outdated_lines[@]}; i++)); do
		local pkg=${outdated_lines[i]%% *}
		[[ -n $pkg ]] && outdated_packages+=("$pkg")
	done
	((${#outdated_packages[@]} == 0)) && {
		log "No outdated Python packages found"
		return 0
	}
	log "Found outdated Python packages, proceeding with upgrades"
	local package
	for package in "${outdated_packages[@]}"; do
		[[ -n $package ]] || continue
		log "Upgrading Python package: $package"
		"${uv_cmd[@]}" pip install -U "$package" 2>/dev/null || log "Warning: Failed to upgrade package $package, continuing"
	done
}

trap error ERR
trap cleanup EXIT INT TERM HUP

command -v uv &>/dev/null || [[ -x $UV_BIN ]] || {
	log "Fatal: $UV_BIN not found or not executable"
	exit 1
}

lock
rotate
exec > >(exec tee -a "$LOGFILE") 2>&1
export LC_ALL=C
{
	upgrade_tools
	upgrade_packages
	log "Operation completed"
} || error

#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n'

[[ -x $(command -v rg) ]] || {
	printf '\x1b[31m ripgrep required: brew install ripgrep\n' >&2
	exit 1
}

declare -A c=(["36.9.2"]=1 ["37.6.0"]=1 ["38.2.0"]=1 ["39.0.0"]=1)

v() {
	local IFS=.
	set -- "$*"
	printf '%03d%03d%03d' "$1" "$2" "${3:-0}"
}

# v() {
# 	local IFS=.
# 	local versions
# 	IFS=. read -r -a versions <<<"$*"
# 	printf '%03d%03d%03d' "${versions[@]:0:3}"
# }

for a in $(mdfind 'kMDItemFSName=="*.app"' 2>/dev/null); do
	[[ -d $a ]] || continue

	while IFS= read -r f; do
		[[ -f $f ]] || continue

		e=$(rg -aoNI --max-count=1 -r '$1' 'Chrome/[^[:space:]]+Electron/([0-9]+(?:\.[0-9]+){1,3})' "$f" 2>/dev/null) ||
			e=$(rg -aoNI --max-count=1 -r '$1' 'Electron/([0-9]+(?:\.[0-9]+){1,3})' "$f" 2>/dev/null) ||
			continue

		IFS=. read -r M m p _ <<<"$e"
		n=${a##*/}
		r=${f#"$a/"}

		if rg -aFq '_cornerMask' "$f" 2>/dev/null; then
			printf '\x1b[31m %-25s Electron %-10s (%s)\n' "$n" "$e" "$r"
		elif [[ $M -gt 39 ||
			($M -eq 39 && -n ${c["39.0.0"]}) ||
			($M -eq 38 && $m -gt 2) ||
			($M -eq 38 && $m -eq 2 && -n ${c["38.2.0"]}) ||
			($M -eq 37 && $m -gt 6) ||
			($M -eq 37 && $m -eq 6 && -n ${c["37.6.0"]}) ||
			($M -eq 36 && $m -gt 9) ||
			($M -eq 36 && $m -eq 9 && ${p:-0} -ge 2 && -n ${c["36.9.2"]}) ]]; then
			printf '\x1b[32m %-25s Electron %-10s (%s)\n' "$n" "$e" "$r"
		else
			printf '\x1b[31m %-25s Electron %-10s (%s)\n' "$n" "$e" "$r"
		fi
		break
	done < <(find "$a" -name 'Electron Framework' -type f 2>/dev/null)
done

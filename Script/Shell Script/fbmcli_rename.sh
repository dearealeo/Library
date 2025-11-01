#!/usr/bin/env bash
set -eEuo pipefail
shopt -s inherit_errexit extglob globstar nullglob dotglob
((${#@} == 1)) || {
	printf 'Usage: %s <target_directory>\n' "${0##*/}" >&2
	exit 1
}
d=${1%/}
[[ -d $d && -r $d && -x $d ]] || {
	printf "Error: '%s' is not a valid accessible directory\n" "$d" >&2
	exit 1
}
export LC_ALL=C
while IFS= read -r -d '' f; do
	b=${f##*/}
	[[ $b =~ \(FBMCLI\.A\.[0-9]+\) ]] || continue
	nb=${b//\(FBMCLI.A.+([0-9]))?( )/}
	[[ $b != "$nb" ]] || continue
	dst=${f%/*}/$nb
	[[ ! -e $dst ]] || {
		printf 'Skipping (target exists): %s\n' "$dst" >&2
		continue
	}
	mv -f -- "$f" "$dst" && printf 'Renamed: %s → %s\n' "$b" "$nb"
done < <(find "$d" -type f -print0)

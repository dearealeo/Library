#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

[ $# -eq 1 ] || { echo "Usage: $0 <target_directory>" >&2; exit 1; }
d=${1%/}
[[ -d $d && -r $d && -x $d ]] || { echo "Error: '$d' is not a valid accessible directory" >&2; exit 1; }

shopt -s nullglob extglob
export LC_ALL=C

while IFS= read -r -d '' f; do
    b=${f##*/}
    [[ $b =~ \(FBMCLI\.A\.[0-9]+\) ]] || continue

    nb=$(echo "$b" | sed -E 's/\(FBMCLI\.A\.[0-9]+\)//g' | sed -E 's/[[:space:]]+$//')

    [[ $b != "$nb" ]] || continue
    dst="${f%/*}/$nb"
    [[ ! -e $dst ]] || { echo "Skipping (target exists): $dst" >&2; continue; }

    mv -f -- "$f" "$dst" && printf 'Renamed: %s → %s\n' "$b" "$nb"
done < <(find "$d" -type f -print0)

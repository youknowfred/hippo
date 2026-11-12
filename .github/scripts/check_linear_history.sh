#!/usr/bin/env bash
# check_linear_history.sh <before> <after> — fail if a push to main carried a merge commit.
#
# main's ruleset requires linear history, and every release is one squash-merge. The ruleset
# has an escape hatch (`gh pr merge --admin`), and a merge commit that lands through it is
# invisible afterwards: nothing looks at main's shape. Four landed that way, and one produced
# two releases that were never tagged. This is the tripwire: it runs after a push to main and
# names every commit in the pushed range that has more than one parent.
#
# <before> may be the all-zero sha GitHub sends for a new branch; then only <after> is checked.
set -uo pipefail

before="${1:-}"
after="${2:-}"
if [ -z "$after" ]; then
  echo "usage: check_linear_history.sh <before> <after>" >&2
  exit 2
fi

range="$before..$after"
case "$before" in
  ""|0000000000000000000000000000000000000000) range="$after^!" ;;
esac
if ! git rev-parse --verify --quiet "$after^{commit}" >/dev/null; then
  echo "linear-history: $after is not a commit in this checkout" >&2
  exit 2
fi
case "$range" in
  *..*)
    if ! git rev-parse --verify --quiet "$before^{commit}" >/dev/null; then
      # The previous tip is gone (a force push). Nothing to walk back to: check the new tip alone.
      range="$after^!"
    fi
    ;;
esac

merges="$(git rev-list --min-parents=2 "$range")" || exit 2
if [ -z "$merges" ]; then
  echo "linear-history: no merge commits in $range"
  exit 0
fi

echo "linear-history: main received merge commit(s). Every change lands as one squash-merge"
echo "(RELEASING.md, step 7); a merge commit means the ruleset was bypassed."
for sha in $merges; do
  git --no-pager log -1 --format='  %h %s (parents: %p)' "$sha"
done
echo "Check that each release in this range was tagged, then say in the next release's"
echo "CHANGELOG entry what happened."
exit 1

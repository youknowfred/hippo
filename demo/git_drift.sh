#!/usr/bin/env bash
# POS-4 — the git-drift staleness hero demo.
#
# A memory cites a function; someone edits that function; hippo flags the memory STALE —
# because the *code it points at moved*, not because a timer expired. No calendar-decay memory tool
# can reproduce this: staleness here is a live `git log` of the cited paths against the commit the
# memory was written at. This script builds the whole scenario from scratch in a throwaway repo.
#
# Run it:  demo/git_drift.sh   (no arguments; leaves nothing behind).
set -euo pipefail

hippo_root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
demo="$(mktemp -d)"
trap 'rm -rf "$demo"' EXIT
cd "$demo"

export PYTHONPATH="$hippo_root/plugin"
export HIPPO_MEMORY_DIR="$demo/.claude/memory"
export HIPPO_TRUST_ALL=1       # skip the trust prompt in this throwaway demo repo
export HIPPO_DISABLE_DENSE=1   # BM25-only: staleness is git-based, no embedding model needed
idx="$demo/.claude/.memory-index"

recall() { python3 -m memory.recall "$1" --memory-dir "$HIPPO_MEMORY_DIR" --index-dir "$idx"; }
staleness() {
  python3 - "$demo" <<'PY'
import os, sys
from memory.session_start import staleness_producer
out = staleness_producer(os.environ["HIPPO_MEMORY_DIR"], sys.argv[1])
print(out if out else "✓ staleness check: every cited file is unchanged since its memory was written.")
PY
}

git init -q && git config user.email demo@example.com && git config user.name demo
mkdir -p src "$HIPPO_MEMORY_DIR"
printf '# Memory Index\n\n## Project\n' > "$HIPPO_MEMORY_DIR/MEMORY.md"

# Pin both commit timestamps with an explicit ≥1s gap, but keep them RECENT — staleness scans
# `git log --since=<window>` and compares commit times (%ct, 1s granularity) with a strict `>`,
# so two commits in the same wall-clock second wouldn't register as drift (the race this avoids),
# and a far-past date would fall outside the scan window entirely. c1 = now−120s, c2 = now−60s:
# a deterministic 60s gap, both well inside the window.
now="$(date +%s)"
export GIT_AUTHOR_DATE="@$((now - 120)) +0000" GIT_COMMITTER_DATE="@$((now - 120)) +0000"

cat > src/auth.py <<'PY'
def rotate_session_token(user):
    """Rotate on every privilege change; 3 retries then hard-fail."""
    return _issue(user, retries=3)
PY
git add -A && git commit -qm "auth: session token rotation"

echo "── 1. write a memory that cites src/auth.py, then index it ──────────────────"
python3 -m memory.new_memory session-token-rotation \
  "session tokens rotate on every privilege change: 3 retries then hard-fail" \
  --type project \
  --body 'Confirmed in src/auth.py — rotate_session_token() retries 3x then hard-fails.' \
  --memory-dir "$HIPPO_MEMORY_DIR" >/dev/null
python3 -m memory.build_index --memory-dir "$HIPPO_MEMORY_DIR" --index-dir "$idx" >/dev/null

echo "── 2. recall it now — found, and NOT stale ─────────────────────────────────"
recall "how do session tokens rotate"
staleness

echo
echo "── 3. someone edits the cited function — the code the memory points at MOVES ─"
cat > src/auth.py <<'PY'
def rotate_session_token(user):
    """Rotate on every privilege change; now 5 retries with exponential backoff."""
    return _issue(user, retries=5, backoff=True)
PY
export GIT_AUTHOR_DATE="@$((now - 60)) +0000" GIT_COMMITTER_DATE="@$((now - 60)) +0000"  # 60s after c1
git add -A && git commit -qm "auth: bump retries 3->5, add backoff"

echo "── 4. nothing about the memory changed — but hippo now flags it STALE ───────"
recall "how do session tokens rotate"
staleness

echo
echo "The memory's own text never changed and no timer expired. hippo flagged it because the CODE"
echo "IT CITES (src/auth.py) moved since it was written — git-drift staleness, verify-before-rely."

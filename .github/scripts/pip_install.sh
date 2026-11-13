#!/usr/bin/env bash
# pip_install.sh <pip install arguments...> — `python -m pip install`, tried three times.
#
# pip retries a request that fails to connect, but a connection that drops while a wheel is
# downloading fails the whole install ("Connection broken: ... Connection reset by peer").
# That failed a CI lane on a change that touched no dependency. A real resolution failure
# still fails the lane: it fails all three tries.
set -u

attempt=1
until python -m pip install "$@"; do
  if [ "$attempt" -ge 3 ]; then
    echo "pip install failed $attempt times; giving up." >&2
    exit 1
  fi
  echo "pip install failed (try $attempt of 3); trying again in $((attempt * 15)) s." >&2
  sleep $((attempt * 15))
  attempt=$((attempt + 1))
done

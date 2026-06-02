#!/usr/bin/env bash
# Thin wrapper for cron. Sets PATH so claude CLI is found.
# The real logic is in cron_update.py.

export PATH="/home/charlie/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

exec /home/charlie/.virtualenvs/arxiv-app/bin/python3 /home/charlie/arxiv/cron_update.py "$@"

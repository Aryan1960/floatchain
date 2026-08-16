#!/usr/bin/env bash
# Pauses the Phase 2 snapshot collector without touching Task Scheduler.
# The scheduled task keeps firing every run; snapshot.py sees the flag file
# and logs a skip instead of polling CSFloat. Resume with resume_collector.sh.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$DIR/.cache"
touch "$DIR/.cache/collector.disabled"
echo "Collector paused. Resume with scripts/resume_collector.sh"

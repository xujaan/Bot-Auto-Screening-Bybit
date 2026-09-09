#!/usr/bin/env sh
set -eu

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

checkpoint_db() {
  db="$1"

  if [ ! -f "$db" ]; then
    echo "skip missing: $db"
    return 0
  fi

  echo "checkpoint: $db"
  sqlite3 "$db" 'PRAGMA busy_timeout=5000; PRAGMA wal_checkpoint(TRUNCATE); PRAGMA integrity_check;'
}

checkpoint_db "$CODEX_HOME/state_5.sqlite"
checkpoint_db "$CODEX_HOME/logs_2.sqlite"

echo "codex flush done"

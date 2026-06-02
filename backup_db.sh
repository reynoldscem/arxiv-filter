#!/usr/bin/env bash
# Daily backup of the arXiv SQLite database.
# Keeps the last 30 days of backups.

set -euo pipefail

DB="/home/charlie/arxiv/app/data/arxiv.db"
BACKUP_DIR="/home/charlie/arxiv/app/data/backups"
DATE=$(date +%Y-%m-%d)
DEST="$BACKUP_DIR/arxiv_${DATE}.db"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB" ]; then
    echo "[$(date)] No database found at $DB, skipping backup."
    exit 0
fi

DB_SIZE=$(stat -c%s "$DB")
echo "[$(date)] Backing up $DB ($DB_SIZE bytes)..."

# Use sqlite3 .backup for a safe copy (handles WAL correctly)
sqlite3 "$DB" ".backup '$DEST'"

BACKUP_SIZE=$(stat -c%s "$DEST")
echo "[$(date)] Backed up to $DEST ($BACKUP_SIZE bytes)"

if [ "$BACKUP_SIZE" -lt 1000 ]; then
    echo "[$(date)] WARNING: backup seems too small ($BACKUP_SIZE bytes)"
fi

# Prune backups older than 30 days
PRUNED=$(find "$BACKUP_DIR" -name "arxiv_*.db" -mtime +30 -print -delete | wc -l)
echo "[$(date)] Pruned $PRUNED backups older than 30 days."

# List current backups
TOTAL=$(find "$BACKUP_DIR" -name "arxiv_*.db" | wc -l)
echo "[$(date)] Total backups on disk: $TOTAL"

#!/bin/bash
set -e

# Run migrations on startup
python manage.py migrate --noinput

# Discover instances from FLOWHISTORY_* env vars
python manage.py discover_instances

# Migrate backup archives into per-instance subdirectories
python manage.py migrate_backup_storage

# Remove backup records whose archive files are missing
python manage.py checkintegrity

SCHEDULER_PID=""
WATCHER_PID=""

# Skip scheduler and watcher in demo mode (ADR 0029) so no real Node-RED is
# contacted, no notification webhooks fire, and no scheduled job runs.
DEMO_MODE_LOWER=$(echo "${DEMO_MODE:-false}" | tr '[:upper:]' '[:lower:]')
if [ "$DEMO_MODE_LOWER" = "true" ] || [ "$DEMO_MODE_LOWER" = "1" ] || [ "$DEMO_MODE_LOWER" = "yes" ]; then
    echo "[entrypoint] DEMO_MODE=true — skipping runapscheduler and runwatcher"
else
    python manage.py runapscheduler &
    SCHEDULER_PID=$!

    python manage.py runwatcher &
    WATCHER_PID=$!
fi

# Forward signals to all child processes
trap 'kill $SCHEDULER_PID $WATCHER_PID $GUNICORN_PID 2>/dev/null; wait; exit 0' SIGTERM SIGINT

# Start gunicorn in background
gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - &
GUNICORN_PID=$!

# Wait for all background processes
wait

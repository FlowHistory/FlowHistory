#!/bin/bash
set -e

# Run migrations on startup
python manage.py migrate --noinput

# Start scheduler in background
python manage.py runapscheduler &
SCHEDULER_PID=$!

# Start file watcher in background
python manage.py runwatcher &
WATCHER_PID=$!

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

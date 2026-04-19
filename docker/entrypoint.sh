#!/bin/sh
set -e

APP_ROLE="${APP_ROLE:-web}"

echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."

while ! python -c "
import os
import socket
host = os.getenv('DB_HOST')
port = int(os.getenv('DB_PORT', '5432'))
s = socket.socket()
s.settimeout(2)
s.connect((host, port))
s.close()
print('PostgreSQL is reachable')
"; do
  echo 'PostgreSQL is unavailable - sleeping'
  sleep 2
done

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

if [ "$APP_ROLE" = "web" ]; then
  echo "Running migrations..."
  python manage.py migrate --noinput

  echo "Collecting static files..."
  python manage.py collectstatic --noinput

  echo "Starting Gunicorn..."
  exec gunicorn ppw.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers 3 \
      --timeout 120
fi

if [ "$APP_ROLE" = "celery_worker" ]; then
  echo "Starting Celery worker..."
  exec celery -A ppw worker --loglevel="${CELERY_LOG_LEVEL:-info}"
fi

if [ "$APP_ROLE" = "celery_beat" ]; then
  echo "Starting Celery beat..."
  exec celery -A ppw beat --loglevel="${CELERY_LOG_LEVEL:-info}"
fi

echo "Unknown APP_ROLE: $APP_ROLE"
exit 1

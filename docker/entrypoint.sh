#!/bin/sh
# Container entrypoint: optional migrations / demo data, then run the command.
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "Applying database migrations..."
    python manage.py migrate --noinput
fi

if [ "${SEED_DEMO_DATA:-0}" = "1" ]; then
    echo "Seeding demo data..."
    python manage.py seed_demo
fi

exec "$@"

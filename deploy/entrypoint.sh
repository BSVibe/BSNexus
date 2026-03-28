#!/bin/bash
set -e

echo "Running database migrations..."
cd /app/backend && python -m alembic upgrade head

exec "$@"

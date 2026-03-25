#!/bin/bash
set -e

echo "Running database migrations..."
cd /app/backend && alembic upgrade head

exec "$@"

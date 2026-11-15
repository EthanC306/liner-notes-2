#!/bin/sh
set -e
# Seed the demo metadata cache (safe to repeat: rows are merged, not duplicated).
python -c "import sys; sys.path.insert(0, 'scripts'); import make_sample_export as m; m.seed_db()"
cd backend
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers

#!/bin/bash
# Vercel build command.
# Requires SECRET_KEY and DATABASE_URL (Neon integration) env vars to be set.
pip install -r requirements.txt
python manage.py migrate --no-input
python manage.py collectstatic --no-input

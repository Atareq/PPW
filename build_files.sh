#!/bin/bash
# Run this as the Vercel "Build Command" in project settings.
# Requires SECRET_KEY and DATABASE_URL env vars to be set first.
pip install -r requirements.txt
python manage.py collectstatic --no-input

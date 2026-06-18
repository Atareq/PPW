"""
reset_prod_db.py — Drop all tables in the production Neon DB and re-run every
migration from scratch.

WARNING: This destroys all production data. Only use when you intentionally
want a clean schema (e.g. after modifying initial migrations in-place).

Usage (from PPW/ directory):
    python scripts/reset_prod_db.py
"""

import os
import sys
from pathlib import Path

# Add project root to path so Django is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load production env vars
try:
    from dotenv import dotenv_values
except ImportError:
    print("ERROR: python-dotenv not installed. Run: pip install python-dotenv")
    sys.exit(1)

env_file = ROOT / ".env.production"
if not env_file.exists():
    print(f"ERROR: {env_file} not found. Run: vercel env pull .env.production --environment production")
    sys.exit(1)

env = dotenv_values(env_file)
for k, v in env.items():
    os.environ[k] = v

# Always use the unpooled connection for DDL (Neon requirement)
unpooled = env.get("DATABASE_URL_UNPOOLED")
if unpooled:
    os.environ["DATABASE_URL"] = unpooled
    print("Using DATABASE_URL_UNPOOLED for DDL safety.")
else:
    print("WARNING: DATABASE_URL_UNPOOLED not found, falling back to DATABASE_URL.")

import django
from django.core.management import call_command

django.setup()

from django.db import connection

# ── Safety prompt ────────────────────────────────────────────────────────────
print("\n*** DANGER: This will DROP ALL TABLES in the production database. ***")
print("   All existing data will be permanently lost.")
print("   The schema will be rebuilt from scratch by running all migrations.\n")
answer = input("Type 'RESET' (all caps) to confirm: ").strip()
if answer != "RESET":
    print("Aborted — no changes made.")
    sys.exit(0)

# ── Drop everything ──────────────────────────────────────────────────────────
print("\n[1/3] Dropping all tables (DROP SCHEMA public CASCADE)...")
with connection.cursor() as c:
    c.execute("DROP SCHEMA public CASCADE")
    c.execute("CREATE SCHEMA public")
    c.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
print("      Done.")

# ── Recreate schema via migrations ───────────────────────────────────────────
print("\n[2/3] Running all migrations from scratch...")
call_command("migrate", "--no-input", verbosity=1)
print("      Done.")

# ── Verify ───────────────────────────────────────────────────────────────────
print("\n[3/3] Verifying — checking for unapplied migrations...")
call_command("migrate", "--check", verbosity=0)
print("      All migrations applied. Production DB is clean and up to date.")

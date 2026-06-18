# CLAUDE.md — PPW Backend

This file provides guidance to Claude Code when working with the PPW Django backend.

## Project Overview

**PPW** is a Django 4.2 + DRF backend for Pigeon Planet — a pigeon marketplace with auctions, fixed-price market, loft management, loyalty economy, and a web admin panel. Full endpoint reference is in [AGENTS.md](AGENTS.md).

## Common Commands

```bash
# Activate virtualenv (Windows)
venv\Scripts\activate

# Apply migrations (always run before runserver)
python manage.py migrate

# Start dev server
python manage.py runserver

# Run tests
pytest

# Make migrations after model changes
python manage.py makemigrations
```

## Architecture

- **Apps:** `apps/` — one Django app per domain (`auth`, `users`, `assets`, `auctions`, `orders`, `payments`, `complaints`, `chat`, `feed`, `loyalty`, `ratings`, `races`, `packages`, `promotions`, `insights`, `pedigrees`, `core`)
- **Permissions:** `ppw/permissions.py` — `PERMISSION_MAP` dict controls which profile types can call which actions per viewset
- **API root:** `ppw/urls.py` — all routes under `/api/`
- **Base URL:** `https://ppw-backend.vercel.app/api`

## Source of Truth

- **[AGENTS.md](AGENTS.md)** — complete endpoint reference, payload shapes, business rules, actor permissions, planned phases. Read before touching any module.
- **[BACKEND_CHANGELOG.md](BACKEND_CHANGELOG.md)** — dated log of every backend change made.
- **[../MOBILE_BACKEND_AUDIT.md](../MOBILE_BACKEND_AUDIT.md)** — mobile vs backend alignment status across all modules.

---

## Mandatory: Update Docs After Every Change

After every comparison, fix, or backend change — no exceptions:

1. **`BACKEND_CHANGELOG.md`** — add a dated section listing every file touched, what changed, and why. Use the existing format: `## YYYY-MM-DD — <short description>` with sub-sections per file.

2. **`../MOBILE_BACKEND_AUDIT.md`** — if the change affects a mobile-facing endpoint (customer or seller scope):
   - Update the affected module's table row (❌ → ✅, resolve ⚠️/🐛)
   - Update the Module Progress table (Last Worked date + Status)
   - Add a dated strikethrough entry to Critical Gaps for anything newly resolved

These updates are part of completing the task, not optional housekeeping. Never finish a backend change without them.

---

## Permission Pattern

Permissions are declared in `ppw/permissions.py` as:

```python
PERMISSION_MAP = {
    "ViewSetClassName": {
        "action_name": [ProfileType.CUSTOMER, ProfileType.SELLER],
        ...
    }
}
```

When adding a new action or endpoint, add its permission entry here before testing. Missing entries default to deny.

## Scope — Customer & Seller vs Manager

Mobile app endpoints serve customers and sellers only. Manager-only endpoints are for the dashboard (`lotfy-dashboard`). When a change affects both, note which scope each part targets in the changelog.

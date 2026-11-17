# PPW: Pigeon Auction and Marketplace Backend

Backend for a racing-pigeon platform: breeders auction birds, sell supplies
in a marketplace, and follow races, seasons and pedigrees. One account can
switch between a customer profile and a seller profile.

Python 3.11 · Django 4.2 · Django REST Framework · PostgreSQL · Celery ·
Redis · Django Channels · Firebase Cloud Messaging · Docker Compose

## What it does

- **Auctions:** single-bird, pair, multi-bird and racing-group listings,
  public or silent (sealed-bid), with minimum increments, reserve and buy-now
  prices, optional auto-extension, and a Celery Beat job that closes expired
  auctions every minute.
- **Marketplace:** cart, checkout, per-item seller approval, and stock that
  is reduced only when a payment is approved.
- **Payments:** manual payment requests. The buyer uploads proof of payment,
  the seller approves or rejects it, and approval transfers ownership.
  There is no card gateway.
- **Engagement:** comments and ratings, follows and a feed, chat, loyalty
  points and badges, referrals, prize draws, a lucky wheel and seasons.
- **Pedigrees:** pedigree registration and OCR-based pedigree reading
  (OpenCV and Tesseract).
- **Notifications:** stored in the database, pushed through FCM, and sent
  live to a per-profile WebSocket at `/ws/notifications/`.

## Architecture

A Django monolith with one app per domain under [`apps/`](apps/).

- **Business rules live in service classes** (`apps/*/services.py`); views
  stay thin.
- **Access control:** each user has customer, seller and manager profiles.
  `HasAppAccess` checks the active profile's role against an action matrix
  in [`apps/general/constants.py`](apps/general/constants.py), and managers
  have their own permission map.
- **Async work:** Celery on Redis runs push delivery, chat and loyalty side
  effects and the scheduled jobs. Channels serves the notification socket.
- **Runtime:** gunicorn with Uvicorn workers on ASGI, Nginx in front in
  production. The container entrypoint runs migrations and `collectstatic`.

## Reliability

- **Bids** are re-validated under a row lock on the auction item
  (`BiddingService.place_bid`), covered by `test_bid_is_revalidated_under_lock`.
- **Closing an auction** locks the auction and its items and returns early
  if it is already closed (`test_close_auction_is_idempotent`).
- **Payment approval** is a locked state machine. Ownership transfer and
  stock reduction happen inside the approval transaction.
- **Side effects** (notifications, push, loyalty) run in
  `transaction.on_commit`, and notifications carry an idempotency key backed
  by a unique constraint.
- Money uses `Decimal` throughout.

## Testing

About 1,900 pytest tests across the apps:

```bash
pip install -r requirements/dev.txt
pytest
```

Tests use in-memory SQLite with migrations disabled, so they do not exercise
PostgreSQL row locks. Assertions are written in English; the i18n tests
switch to Arabic, the production default, explicitly.

## Running locally

```bash
cp .env.example .env
docker compose -f compose.yaml -f compose.local.yaml up --build
```

Local Compose runs PostgreSQL (`db`), Redis, the app, a Celery worker and
Celery Beat. Migrations run on container start. For production, create
`.env.prod` from the same template with
`DJANGO_SETTINGS_MODULE=ppw.settings.production` and `DEBUG=False`, then:

```bash
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
```

Production Compose adds Nginx, which also serves `/media/`.

Without Docker, use Python 3.11 and a local PostgreSQL configured through the
`DB_*` values in `.env`:

```bash
pip install -r requirements/dev.txt
python manage.py migrate
python manage.py runserver
```

### Creating a superuser

```python
# python manage.py shell_plus
from apps.authentication.models import UserAccount

UserAccount.objects.create_superuser(
    username="super_user",
    phone_number="012345678",
    password="change-me",
)
```

API tokens carry the active profile, so create a profile for the user
before calling business endpoints.

## Development tools

```bash
pre-commit install
pre-commit run --all-files
```

The hooks run black, isort, flake8, and the test suite.

## Limitations

- Bidding is plain REST; clients learn about new bids through notifications,
  not a live bidding stream.
- Payments are confirmed manually by sellers.
- Uploaded files are stored on the server's disk (`MEDIA_ROOT`), not in
  object storage.
- The test database does not prove locking behaviour (see Testing).

## License

The source is public for review. No open-source license is granted.

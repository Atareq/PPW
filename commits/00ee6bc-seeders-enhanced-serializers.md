# Commit: 00ee6bc — added seeders / enhanced serializers
**Date:** 2026-06-18  
**Author:** Ahmed Ali

---

## Summary of Changes

| Area | Change Type | Description |
|------|-------------|-------------|
| Assets | Updated | `seller_nickname` added to `BirdSerializer` |
| Auctions | Updated | `is_active`, `is_ended`, `buy_now_price` added to `AuctionListSerializer` |
| Auctions | Updated | `allow_scoped_read_access = True` on `AuctionViewSet` |
| Feed | Updated | `avatar_url` added to seller follow and suggestion serializers |
| Users (Model) | New | `photo` URLField added to `Profile` base model |
| Users (Serializer) | Updated | `avatar_url` (writable) added to seller and customer mobile serializers |
| Complaints (Migration) | Updated | `complaint_reopened` event type registered in DB |
| Dev Tooling | New | Seed management commands + `reset_prod_db.py` script (no API impact) |

---

## 1. Assets — `seller_nickname` in BirdSerializer

**File:** `apps/assets/serializer.py`

### What changed
`BirdSerializer` now exposes `seller_nickname` — the seller's display nickname pulled from `owner.sellerprofile.nickname`. Returns `""` if the owner has no seller profile.

### Old Response (partial)
```json
{
  "id": 1,
  "name": "Eagle",
  "owner": 5,
  ...
}
```

### New Response (partial)
```json
{
  "id": 1,
  "name": "Eagle",
  "owner": 5,
  "seller_nickname": "FalconBreeder",
  ...
}
```

---

## 2. Auctions — `AuctionListSerializer` additions

**File:** `apps/auctions/serializers.py`

### New fields: `is_active`, `is_ended`, `buy_now_price`

`is_active` and `is_ended` are computed via `SerializerMethodField` calling the model's `.is_active()` / `.is_ended()` methods.  
`buy_now_price` was already on the model but not included in the list fields — now exposed.

### Old List Item (partial)
```json
{
  "id": 3,
  "title": "Spring Auction",
  "status": "active",
  "buy_now_enabled": true,
  "time_remaining": 3600
}
```

### New List Item (partial)
```json
{
  "id": 3,
  "title": "Spring Auction",
  "status": "active",
  "buy_now_enabled": true,
  "buy_now_price": "2000.00",
  "time_remaining": 3600,
  "is_active": true,
  "is_ended": false
}
```

---

## 3. Auctions — `allow_scoped_read_access`

**File:** `apps/auctions/views.py`

`allow_scoped_read_access = True` added to `AuctionViewSet`. This allows read actions to pass through `HasAppAccess` even when the caller's scope would normally restrict them (e.g. viewing auctions without a full AUCTION write permission).

---

## 4. Feed — `avatar_url` in Seller Serializers

**File:** `apps/feed/serializers.py`

### `SellerFollowSellerSerializer`
`avatar_url` added — reads from `instance.photo`, defaults to `""`.

### Old Response
```json
{
  "id": 7,
  "nickname": "FalconBreeder",
  "country": "EG",
  "currency": "EGP",
  "is_followed": true
}
```

### New Response
```json
{
  "id": 7,
  "nickname": "FalconBreeder",
  "country": "EG",
  "currency": "EGP",
  "avatar_url": "https://cdn.example.com/avatars/7.jpg",
  "is_followed": true
}
```

---

### `SellerSuggestionSerializer`
`avatar_url` added as a `URLField` sourced from `photo`.

### Old Response
```json
{
  "id": 8,
  "nickname": "PigeonKing",
  "avg_rating": 4.5,
  "ratings_count": 12,
  "is_followed": false
}
```

### New Response
```json
{
  "id": 8,
  "nickname": "PigeonKing",
  "avg_rating": 4.5,
  "ratings_count": 12,
  "avatar_url": "https://cdn.example.com/avatars/8.jpg",
  "is_followed": false
}
```

---

## 5. Users — `photo` Field on Profile Model

**Files:** `apps/users/models.py`, `apps/users/migrations/0002_profile_photo.py`

New `photo` URLField (`max_length=500`, blank, default `""`) added to the `Profile` base model (shared by `SellerProfile` and `CustomerProfile`).

---

## 6. Users — `avatar_url` in Mobile Serializers

**File:** `apps/users/serializers.py`

Both `SellerProfileMobileSerializer` and `CustomerProfileMobileSerializer` now expose `avatar_url` as a **writable** field (sourced from `profile.photo`).

### Old Response / Request
```json
{
  "id": 5,
  "nickname": "FalconBreeder",
  "country": "EG",
  ...
}
```

### New Response / Request
```json
{
  "id": 5,
  "nickname": "FalconBreeder",
  "country": "EG",
  "avatar_url": "https://cdn.example.com/avatars/5.jpg",
  ...
}
```

> `avatar_url` is writable — clients can update the profile photo by PATCHing this field.

---

## 7. Migrations

| Migration | What |
|-----------|------|
| `users/0002_profile_photo` | Adds `photo` URLField to `Profile` |
| `complaints/0003_alter_complaintevent_event_type` | Registers `complaint_reopened` choice in DB (backfills the `REOPENED` event type added in commit `a9ed138`) |

---

## 8. Dev Tooling (No API Impact)

New Django management commands and a utility script for seeding and resetting the database in development/staging:

| File | Purpose |
|------|---------|
| `seed_all.py` | Master seeder — runs all seed commands in order |
| `seed_assets.py` | Seeds birds and product assets |
| `seed_auctions_orders.py` | Seeds auctions and orders |
| `seed_bids_loyalty_promotions_notifications.py` | Seeds bids, badges, promotions, notifications |
| `seed_races_reviews_payments.py` | Seeds races, reviews, payment requests |
| `scripts/reset_prod_db.py` | Utility to wipe and re-seed a staging/prod DB |

---

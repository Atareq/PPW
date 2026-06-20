# Commit: a9ed138 — added vercel prod && fixes
**Date:** 2026-06-18  
**Author:** Ahmed Ali

---

## Summary of Changes

| Area | Change Type | Description |
|------|-------------|-------------|
| Assets | Updated | `rating` + `review_count` added to Bird and ProductAsset responses |
| Auctions | Updated | Richer bird info in item detail; new `is_owner`, `thumbnail_url`, `min_bid_increment`, `winner_payment_request_id` fields |
| Auctions | Updated | `is_visible` is now editable (manager-only) via PATCH auction |
| Chat | Updated | `unread_count` added to conversation list |
| Complaints | New | `POST /complaints/<pk>/reopen/` endpoint |
| Loyalty | Updated | Managers can query any user's point balance via `?user_id=` |
| Orders | Updated | `OrderItem` list now filterable by `discount_offer` and `cashback_offer` |
| Packages | New | `POST /packages/my-packages/<pk>/cancel/` endpoint |
| Pedigrees | Updated | Documents endpoint now supports `GET` list with `?bird_id=` filter |
| Promotions | Updated | Managers can query any customer's cashback balance via `?customer=` |
| Users | New | Bulk suspend/restore/activate actions for seller and customer profiles (admin only) |
| Infra | New | Vercel deployment config (vercel.json, settings/vercel.py, api/index.py) |

---

## 1. Assets — `rating` and `review_count`

**Files:** `apps/assets/serializer.py`, `apps/assets/views.py`, `apps/assets/constants.py`

### What changed
Both `BirdSerializer` and `ProductAssetSerializer` now expose `rating` and `review_count`.  
Views annotate the queryset with `Avg` and `Count` on `asset_ratings` to avoid N+1 queries; serializers fall back to a direct aggregate if the annotation is absent.

### Old Response (Bird / ProductAsset)
```json
{
  "id": 1,
  "name": "Eagle",
  "price": "500.00",
  ...
}
```

### New Response (Bird / ProductAsset)
```json
{
  "id": 1,
  "name": "Eagle",
  "price": "500.00",
  "rating": 4.3,
  "review_count": 17,
  ...
}
```

---

## 2. Auctions — Multiple Serializer Enrichments

**File:** `apps/auctions/serializers.py`

### 2a. `AuctionItemDetailSerializer` — richer bird + `winner_payment_request_id`

#### New serializer: `BirdAuctionSerializer`
Replaces `BirdBasicSerializer` inside `AuctionItemDetailSerializer`.  
Adds: `media`, `seller_id`, `seller_nickname`, `ring_number`, `gender`, `colour`, `birthday`, `achievements`, `flying_speed`, `stamina_ability`.

#### Old `bird` / `paired_bird` fields in item detail
```json
{
  "bird": {
    "id": 5,
    "name": "Champion"
  }
}
```

#### New `bird` / `paired_bird` fields in item detail
```json
{
  "bird": {
    "id": 5,
    "name": "Champion",
    "ring_number": "EG-001",
    "gender": "male",
    "colour": "white",
    "birthday": "2022-03-10",
    "achievements": "...",
    "flying_speed": 90,
    "stamina_ability": 85,
    "description": "...",
    "price": "1500.00",
    "media": [...],
    "seller_id": 12,
    "seller_nickname": "FalconBreeder"
  }
}
```

#### New field: `winner_payment_request_id`
```json
{
  "winner_payment_request_id": 42
}
```
Returns `null` if no payment request exists yet.

---

### 2b. `AuctionDetailSerializer` — `is_owner` + `min_bid_increment`

#### Old Response (partial)
```json
{
  "id": 3,
  "title": "Spring Auction",
  "buy_now_price": "2000.00",
  ...
}
```

#### New Response (partial)
```json
{
  "id": 3,
  "title": "Spring Auction",
  "buy_now_price": "2000.00",
  "min_bid_increment": "50.00",
  "is_owner": true,
  ...
}
```

---

### 2c. `AuctionListSerializer` — `current_price`, `buy_now_enabled`, `min_bid_increment`, `thumbnail_url`, `is_owner`

#### Old List Item
```json
{
  "id": 3,
  "title": "Spring Auction",
  "status": "active",
  "item_count": 5,
  "time_remaining": 3600
}
```

#### New List Item
```json
{
  "id": 3,
  "title": "Spring Auction",
  "status": "active",
  "item_count": 5,
  "time_remaining": 3600,
  "current_price": "1200.00",
  "buy_now_enabled": true,
  "min_bid_increment": "50.00",
  "thumbnail_url": "https://cdn.example.com/birds/1/img.jpg",
  "is_owner": false
}
```

`thumbnail_url` — first image from the first item's bird. `null` if no image.

---

### 2d. `AuctionUpdateSerializer` — `is_visible` (manager-only)

#### PATCH Request — Old allowed fields
```json
{ "title": "New Title", "description": "...", "tags": ["tag1"] }
```

#### PATCH Request — New allowed fields
```json
{ "title": "New Title", "description": "...", "tags": ["tag1"], "is_visible": true }
```

> Sending `is_visible` as a non-manager returns `400`:
> ```json
> { "is_visible": "Only managers can modify this field." }
> ```

---

## 3. Chat — `unread_count` in Conversation List

**File:** `apps/chat/serializers.py`

### What changed
`ConversationListSerializer` now includes the number of unread messages for the requesting profile.

### Old Response Item
```json
{
  "id": 10,
  "last_message": {...},
  "last_message_at": "2026-06-17T10:00:00Z",
  "is_active": true
}
```

### New Response Item
```json
{
  "id": 10,
  "last_message": {...},
  "last_message_at": "2026-06-17T10:00:00Z",
  "unread_count": 3,
  "is_active": true
}
```

---

## 4. Complaints — Reopen Endpoint

**Files:** `apps/complaints/views.py`, `apps/complaints/urls.py`, `apps/complaints/services.py`, `apps/complaints/models.py`

### New Endpoint
```
POST /complaints/<pk>/reopen/
```

**Permission:** `COMPLAINT_ADMIN` (manager only)  
**Request Body:** None  
**Response:** `ComplaintDetailSerializer` (200)

**Business rule:** Only `resolved` or `rejected` complaints can be reopened. Status resets to `open`, `admin_note` cleared.

### New Event Type
`complaint_reopened` added to `ComplaintEventType`.

---

## 5. Loyalty — Manager Balance Lookup

**File:** `apps/loyalty/views.py`

### What changed
`GET /loyalty/balance/` now accepts an optional `?user_id=<id>` query param for managers.

### Old Behavior
Always returned the authenticated user's own balance.

### New Behavior

**Request (own balance — unchanged):**
```
GET /loyalty/balance/
```

**Request (manager viewing another user):**
```
GET /loyalty/balance/?user_id=42
```

**Response (same shape):**
```json
{
  "user_id": 42,
  "active_profile_id": null,
  "balance": 1500
}
```

> Non-managers passing `?user_id` get `403 Forbidden`.

---

## 6. Orders — OrderItem Filtering

**File:** `apps/orders/views.py`

### What changed
`GET /orders/items/` now supports filtering by `discount_offer` and `cashback_offer`.

**New Query Params:**
```
GET /orders/items/?discount_offer=5
GET /orders/items/?cashback_offer=3
```

---

## 7. Packages — Cancel Seller Package

**Files:** `apps/packages/views.py`, `apps/packages/urls.py`

### New Endpoint
```
POST /packages/my-packages/<pk>/cancel/
```

**Permission:** Authenticated seller (own package)  
**Request Body:** None  
**Response:** `SellerPackageSerializer` (200)

---

## 8. Pedigrees — List Documents + `bird_id` Filter

**Files:** `apps/pedigrees/views.py`, `apps/pedigrees/urls.py`

### What changed
`GET /pedigrees/documents/` now works (was `POST`-only before).  
Managers see all; sellers/customers see their own. Optional `?bird_id=` filter for non-managers.

### Old URL behavior
```
POST /pedigrees/documents/   → upload only
```

### New URL behavior
```
GET  /pedigrees/documents/              → list owned documents
GET  /pedigrees/documents/?bird_id=12  → filter by bird
POST /pedigrees/documents/              → upload (unchanged)
```

**GET Response:**
```json
[
  {
    "id": 1,
    "bird": 12,
    "document_url": "...",
    "status": "verified",
    ...
  }
]
```

---

## 9. Promotions — Manager Cashback Balance Lookup

**File:** `apps/promotions/views.py`

### What changed
`GET /promotions/cashback/balance/` now allows managers to query any customer's balance via `?customer=<id>`.

### Old Behavior
Returned `403` for any non-customer profile.

### New Behavior

**Manager request:**
```
GET /promotions/cashback/balance/?customer=7
```

**Response (same shape):**
```json
{
  "customer_id": 7,
  "balance": 250.00
}
```

> Manager without `?customer` → `403`.  
> Invalid customer ID → `404`.

---

## 10. Users — Bulk Profile Actions (Admin Only)

**File:** `apps/users/views.py`

### New Endpoints

| Endpoint | Action |
|----------|--------|
| `POST /admin/sellers/bulk-suspend/` | Set `is_valid=False` for given IDs |
| `POST /admin/sellers/bulk-restore/` | Set `is_valid=True` for given IDs |
| `POST /admin/sellers/bulk-activate/` | Set `activated=True` for given IDs |
| `POST /admin/customers/bulk-suspend/` | Set `is_valid=False` for given IDs |
| `POST /admin/customers/bulk-restore/` | Set `is_valid=True` for given IDs |
| `POST /admin/customers/bulk-activate/` | Set `activated=True` for given IDs |

**Permission:** `IsAdminUser` (Django staff/superuser)

**Request Body (all):**
```json
{ "ids": [1, 2, 3] }
```

**Response:**
```json
{ "updated": 3 }
```

---

## 11. Infrastructure — Vercel Deployment

**New files:** `vercel.json`, `ppw/settings/vercel.py`, `api/index.py`, `build_files.sh`, `.vercelignore`, `.python-version`

Adds Vercel serverless deployment support. `api/index.py` exposes the WSGI app. `ppw/settings/vercel.py` overrides database and static file settings for the Vercel environment.

---

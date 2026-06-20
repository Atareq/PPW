# Commit: 3b7d484 — refactored flow based on 12 - 10 - 9 points in the pdf file
**Date:** 2026-06-20  
**Author:** Ahmed Ali

---

## Summary of Changes

| Area | Change Type | Description |
|------|-------------|-------------|
| Auctions (Model) | New | `AuctionComment` model + `chat_enabled` flag on `Auction` |
| Auctions (Service) | New | `AuctionChatService` — post/list comments with access rules and contact-info filtering |
| Auctions (API) | New | `GET/POST /auctions/comments/` endpoints |
| Auctions (Serializer) | Updated | `chat_enabled` added to detail and update serializers |
| Auctions (Service) | Updated | Bidders blocked by the auction owner cannot place bids |
| Chat (Service) | Updated | Customer must follow seller before starting a conversation |
| Feed (API) | New | `GET /feed/sellers/` — paginated seller discovery list for customers |
| Feed (Service) | New | `SellerFollowService.is_following()` helper |
| Permissions | Updated | `AUCTION_COMMENT` resource added for seller and customer |

---

## 1. Auctions — `AuctionComment` Model & `chat_enabled` Flag

**Files:** `apps/auctions/models.py`, `apps/auctions/migrations/0003_auction_chat.py`

### New field on `Auction`
```
chat_enabled  BooleanField  default=False
```
Seller opts-in per auction to enable a public comment thread.

### New model: `AuctionComment`

| Field | Type | Notes |
|-------|------|-------|
| `auction` | FK → `Auction` | |
| `sender` | FK → `Profile` | |
| `body` | CharField(500) | |
| `is_announcement` | BooleanField | `True` only for seller-posted announcements |
| `created` / `modified` | auto | Ordered by `created, id` (oldest first) |

Indexes on `(auction, created)` and `(sender, auction)`.

---

## 2. Auctions — `AuctionChatService`

**File:** `apps/auctions/services.py`

### Access rules
| Who | Can post | Can mark `is_announcement` |
|-----|----------|---------------------------|
| Auction owner (seller) | Always (while `chat_enabled`) | Yes |
| Customer with ≥1 bid on any item in the auction | Yes | No |
| Any other profile | No (`400`) | No |

### Contact-info filtering
Body is scanned for emails, URLs, WhatsApp/Telegram references, and phone numbers. Match → `400`:
```json
{ "body": "Sharing contact details or external links is not allowed in auction chat." }
```

### `list_comments(auction_id)`
Returns all comments ordered `created, id` (chronological).

---

## 3. Auctions — Comment Endpoints

**Files:** `apps/auctions/views.py`, `apps/auctions/urls.py`

### New endpoints (registered under `auctions/comments/`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/auctions/comments/?auction_id=<id>` | List comments for an auction (paginated) |
| `POST` | `/auctions/comments/` | Post a comment |

**Permission resource:** `AUCTION_COMMENT`

### `GET /auctions/comments/?auction_id=<id>`

**Response:**
```json
{
  "results": [
    {
      "id": 1,
      "body": "Great birds!",
      "is_announcement": false,
      "sender_id": 12,
      "sender_nickname": "FalconBreeder",
      "sender_type": "Seller",
      "is_mine": true,
      "created": "2026-06-20T10:00:00Z"
    }
  ],
  "count": 1,
  "next": null,
  "previous": null
}
```

`is_mine` — `true` when the requesting profile is the comment sender.  
`sender_nickname` — from `sender.sellerprofile.nickname`; returns `""` for customers.

### `POST /auctions/comments/`

**Request:**
```json
{
  "auction_id": 3,
  "body": "When does bidding open?",
  "is_announcement": false
}
```

**Response (201):** Same shape as a single item in the list above.

**Error cases:**

| Condition | Response |
|-----------|----------|
| `chat_enabled=False` | `400 {"auction": "Chat is not enabled for this auction."}` |
| Customer with no bids | `400 {"sender": "You must place a bid before participating..."}` |
| Non-seller sets `is_announcement=true` | `400 {"is_announcement": "Only the auction seller can post announcements."}` |
| Contact info in body | `400 {"body": "Sharing contact details..."}` |

---

## 4. Auctions — `chat_enabled` in Serializers

**File:** `apps/auctions/serializers.py`

### `AuctionDetailSerializer`
`chat_enabled` added to the read response.

### Old Response (partial)
```json
{
  "id": 3,
  "title": "Spring Auction",
  "is_visible": true
}
```

### New Response (partial)
```json
{
  "id": 3,
  "title": "Spring Auction",
  "is_visible": true,
  "chat_enabled": false
}
```

### `AuctionUpdateSerializer`
`chat_enabled` added as a writable field — any authenticated seller (auction owner) can toggle it via PATCH.

### PATCH Request
```json
{ "chat_enabled": true }
```

---

## 5. Auctions — Block Check on Bid

**File:** `apps/auctions/services.py` — `BidBusinessValidator._validate_bidder`

### What changed
Before placing a bid, the service now checks if the bidder (customer) is blocked by the auction owner. If a block exists in either direction, the bid is rejected.

### Old Behavior
Only validated that the bidder was a `Customer` profile.

### New Behavior
```
400 "You cannot bid on this auction while a block is active."
```
Triggered when `BlockService.is_blocked_between(bidder, auction.owner)` returns `True`.

---

## 6. Chat — Follow-Gate on Conversation Start

**File:** `apps/chat/services.py`

### What changed
`ChatService` now requires the customer to **follow the seller** before a new conversation can be started.

### Old Behavior
Any customer could initiate a conversation with any seller (subject only to block checks).

### New Behavior
```json
{
  "receiver_profile_id": "You must follow this seller before starting a conversation."
}
```
Uses the new `SellerFollowService.is_following(customer, seller)` helper.

---

## 7. Feed — Seller Discovery List

**Files:** `apps/feed/views.py`, `apps/feed/urls.py`, `apps/feed/serializers.py`

### New Endpoint
```
GET /feed/sellers/
```

Paginated list of all active, valid sellers — excluding blocked sellers and the requesting user's own seller profile.

**Permission:** `IsAuthenticated`

**Query Params:**

| Param | Default | Max | Description |
|-------|---------|-----|-------------|
| `page` | `1` | — | Page number |
| `page_size` | `20` | `50` | Results per page |

**Response:**
```json
{
  "results": [
    {
      "id": 7,
      "nickname": "FalconBreeder",
      "country": "EG",
      "avg_rating": "4.50",
      "ratings_count": 23,
      "avatar_url": "https://cdn.example.com/avatars/7.jpg"
    }
  ],
  "count": 120,
  "page": 1,
  "page_size": 20,
  "has_more": true
}
```

Ordered by `-avg_rating, -created`.

---

## 8. Feed — `SellerFollowService.is_following()`

**File:** `apps/feed/services.py`

New static helper:
```python
SellerFollowService.is_following(customer_profile, seller_profile) -> bool
```
Returns `True` if an active `SellerFollow` record exists between the customer and seller. Accepts profile instances or PKs.

---

## 9. Permissions — `AUCTION_COMMENT` Resource

**File:** `apps/general/constants.py`

Added to both `SELLER_PERMISSIONS` and `CUSTOMER_PERMISSIONS`:
```python
"AUCTION_COMMENT": ["list", "create"]
```

---

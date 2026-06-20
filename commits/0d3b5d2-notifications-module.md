# Commit: 0d3b5d2 — added notifications module
**Date:** 2026-06-18  
**Author:** Ahmed Ali

---

## Summary of Changes

| Area | Change Type | Description |
|------|-------------|-------------|
| Core | New | `Notification` model, serializers, service, views, URLs — full notifications module |
| Auctions | Updated | Notifications fired on new bid, auction won (settlement + buy-now) |
| Chat | Updated | Notification created when a chat message is delivered |
| Loyalty | Updated | Notification created when a badge is awarded (first-time only) |
| Orders | Updated | Notifications on checkout, seller approve/reject, auto-cancel |
| Payments | Updated | Notifications on payment request approved/rejected |
| Permissions | Updated | `NOTIFICATION` resource added to seller and customer permission maps |

---

## 1. New Module — `apps/core` (Notifications)

### 1a. Model: `Notification`

**File:** `apps/core/models.py`

| Field | Type | Notes |
|-------|------|-------|
| `profile` | FK → `users.Profile` | Recipient |
| `kind` | CharField (choices) | See kinds below |
| `title` | CharField(200) | Short heading |
| `body` | TextField | Optional detail |
| `is_read` | BooleanField | Default `False`, db_index |
| `read_at` | DateTimeField | Null until marked read |
| `metadata` | JSONField | Arbitrary context (IDs etc.) |
| `profile_type` | CharField | `"Customer"` / `"Seller"` / `"All"` |

**Notification kinds (`NotificationKind`):**

| Value | Label |
|-------|-------|
| `order` | Order update |
| `auction_won` | Auction won |
| `new_bid` | New bid on item |
| `bid_accepted` | Bid accepted |
| `new_badge` | Badge awarded |
| `payment` | Payment update |
| `chat` | New message |

---

### 1b. Endpoints

| Method | URL | Who | Description |
|--------|-----|-----|-------------|
| `GET` | `/notifications/` | Seller / Customer / Manager | List notifications |
| `POST` | `/notifications/` | Manager | Create a notification manually |
| `GET` | `/notifications/unread-count/` | Seller / Customer | Get unread count |
| `POST` | `/notifications/<pk>/read/` | Seller / Customer | Mark one as read |
| `DELETE` | `/notifications/<pk>/` | Seller / Customer / Manager | Hard-delete a notification |

---

### 1c. `GET /notifications/`

**Query params:**

| Param | Who | Description |
|-------|-----|-------------|
| `is_read` | All | `true` or `false` |
| `profile_id` | Manager only | Filter by profile |
| `kind` | Manager only | Filter by notification kind |

**Response:**
```json
[
  {
    "id": 1,
    "kind": "order",
    "title": "New order item",
    "body": "A customer placed an order for one of your items.",
    "is_read": false,
    "read_at": null,
    "metadata": {
      "order_id": 10,
      "order_item_id": 5,
      "asset_id": 3
    },
    "profile_type": "Seller",
    "created": "2026-06-18T10:00:00Z"
  }
]
```

---

### 1d. `POST /notifications/` (Manager)

**Request:**
```json
{
  "profile_id": 42,
  "kind": "order",
  "title": "Custom notification",
  "body": "Something happened.",
  "metadata": { "order_id": 7 },
  "profile_type": "Customer"
}
```

**Response (201):** Same shape as list item above.

---

### 1e. `GET /notifications/unread-count/`

**Response:**
```json
{ "count": 5 }
```

---

### 1f. `POST /notifications/<pk>/read/`

**Request Body:** None  
**Response (200):** Full `NotificationSerializer` with `is_read: true` and `read_at` populated.

---

### 1g. `DELETE /notifications/<pk>/`

**Response:** `204 No Content`  
Hard-delete (bypasses soft-delete).

---

### 1h. Permissions

Added `NOTIFICATION` resource to both `SELLER_PERMISSIONS` and `CUSTOMER_PERMISSIONS`:
- Allowed actions: `list`, `mark_read`, `unread_count`
- Managers get `create` and `destroy` via admin access.

---

## 2. Auctions — Notification Triggers

**File:** `apps/auctions/services.py`

### New bid placed → notify auction owner (Seller)
Fired inside `BiddingService` after a bid is recorded.

```json
{
  "kind": "new_bid",
  "title": "New bid",
  "body": "A customer placed a new bid on your auction item.",
  "metadata": {
    "auction_id": 3,
    "item_id": 7,
    "bid_id": 21,
    "amount": "500.00"
  },
  "profile_type": "Seller"
}
```

### Auction settled — winner notification (Customer)
Fired in `SettlementService._settle_item` only when the winner changes or item becomes SOLD for the first time.

```json
{
  "kind": "auction_won",
  "title": "Auction won",
  "body": "You won an auction item.",
  "metadata": {
    "auction_id": 3,
    "item_id": 7,
    "bid_id": 21,
    "amount": "500.00"
  },
  "profile_type": "Customer"
}
```

### Buy-now purchased → notify buyer (Customer)
Fired in `SettlementService.trigger_buy_now`.

```json
{
  "kind": "auction_won",
  "title": "Auction won",
  "body": "You bought this auction item with buy now.",
  "metadata": {
    "auction_id": 3,
    "item_id": 7,
    "bid_id": 22,
    "amount": "2000.00"
  },
  "profile_type": "Customer"
}
```

---

## 3. Chat — Message Notification

**File:** `apps/chat/tasks.py`

Previously the Celery task `send_chat_message_notification` was a placeholder that only logged. Now it creates a real notification for the message receiver.

```json
{
  "kind": "chat",
  "title": "New message",
  "body": "<first 140 chars of message body>",
  "metadata": {
    "conversation_id": 5,
    "message_id": 99,
    "sender_id": 12
  },
  "profile_type": "<receiver's profile type>"
}
```

Task return value now includes `notification_id`.

---

## 4. Loyalty — Badge Notification

**File:** `apps/loyalty/services.py`

When a badge is awarded for the **first time** (`created=True` from `get_or_create`), a notification is sent to the profile.

```json
{
  "kind": "new_badge",
  "title": "Badge awarded",
  "body": "<badge title from BADGE_DEFINITIONS>",
  "metadata": {
    "badge_type": "first_purchase",
    "badge_award_id": 8
  },
  "profile_type": "<profile's type>"
}
```

> No notification if the badge was already awarded (idempotent).

---

## 5. Orders — Notification Triggers

**File:** `apps/orders/services.py`

### Checkout — notify each seller (Seller)
Fired in `OrderCheckoutService.checkout` after order is finalized.

```json
{
  "kind": "order",
  "title": "New order item",
  "body": "A customer placed an order for one of your items.",
  "metadata": {
    "order_id": 10,
    "order_item_id": 5,
    "asset_id": 3
  },
  "profile_type": "Seller"
}
```

### Seller approves item → notify customer (Customer)

```json
{
  "kind": "order",
  "title": "Order item approved",
  "body": "Seller approved your order item. Complete the payment request.",
  "metadata": {
    "order_id": 10,
    "order_item_id": 5,
    "asset_id": 3
  },
  "profile_type": "Customer"
}
```

### Seller rejects item → notify customer (Customer)

```json
{
  "kind": "order",
  "title": "Order item rejected",
  "body": "Seller rejected your order item.",
  "metadata": {
    "order_id": 10,
    "order_item_id": 5,
    "asset_id": 3
  },
  "profile_type": "Customer"
}
```

### Auto-cancelled (seller timeout) → notify customer (Customer)

```json
{
  "kind": "order",
  "title": "Order item cancelled",
  "body": "Order item was cancelled because the seller did not act in time.",
  "metadata": {
    "order_id": 10,
    "order_item_id": 5,
    "asset_id": 3,
    "seller_id": 12,
    "timeout_days": 3
  },
  "profile_type": "Customer"
}
```

---

## 6. Payments — Notification Triggers

**File:** `apps/payments/services.py`

### Payment request approved → notify buyer (Customer)

```json
{
  "kind": "payment",
  "title": "Payment approved",
  "body": "Seller approved your payment request.",
  "metadata": {
    "payment_request_id": 15,
    "asset_id": 3,
    "request_type": "order"
  },
  "profile_type": "Customer"
}
```

### Payment request rejected → notify buyer (Customer)

```json
{
  "kind": "payment",
  "title": "Payment rejected",
  "body": "Seller rejected your payment request.",
  "metadata": {
    "payment_request_id": 15,
    "asset_id": 3,
    "request_type": "order"
  },
  "profile_type": "Customer"
}
```

---

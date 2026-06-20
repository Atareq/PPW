# Requirements Audit — Section 16 (Notifications)

> Section: 16 — Notification System
> Audit date: 2026-06-20
> Status: Partial — 2 of 8 notification types exist; all customization features missing

---

## Extracted Requirements

### 16.1 — Notification Types

| # | Type (AR) | Type (EN) | Target | Timing |
|---|-----------|-----------|--------|--------|
| 1 | طير جديد | New Bird | Seller's followers | Immediate on asset listing |
| 2 | بائع جديد | New Seller | Followers (platform subscribers) | Immediate on seller registration |
| 3 | تذكير بالمزاد | Auction Reminder | Interested parties (bidders) | 1 hour before + 10 min before end |
| 4 | فوز بالمزاد | Auction Win | Winner only | Immediate on auction close |
| 5 | طلب دفع العربون | Deposit Payment Request | Winner | 60 min after auction win |
| 6 | إلغاء الفوز | Win Cancellation | Winner + Seller | On payment failure / timeout |
| 7 | مبروك النقاط | Points Congratulations | User who earned points | Immediate on point earn |
| 8 | تذكير الشجرة | Pedigree Reminder | Users interested in pedigree | Custom / configurable timing |

### 16.2 — Notification Customization

| # | Requirement |
|---|-------------|
| A | Each user can enable/disable each notification type individually |
| B | User can choose seller mode or buyer mode per notification type |
| C | User can set a Do Not Disturb (DND) time window |

---

## Current Backend State

### Notification Model (`apps.core.models`)

- `NotificationKind` choices: `order`, `auction_won`, `new_bid`, `bid_accepted`, `new_badge`, `payment`, `chat`
- `Notification` model: profile FK, kind, title, body, is_read, read_at, metadata, profile_type
- `NotificationService`: create, mark_read, unread_count — all synchronous, no scheduling

### Follower Infrastructure (`apps.feed.models`)

- `SellerFollow` model exists: `follower (CustomerProfile) → seller (SellerProfile)`, with `is_active` flag
- Can be used to fan-out "new bird" notifications to followers — but the hook is not wired

### Scheduling

- **No Celery, no task queue, no beat scheduler** — the entire backend is synchronous
- Timed notifications (1hr/10min before auction, 60min after win) require async task infrastructure

---

## Gap Analysis

### 16.1 — Notification Types

| # | Requirement | Status | Gap |
|---|-------------|--------|-----|
| 1 | New Bird notification to followers | ❌ Missing | No `new_bird` kind. `SellerFollow` model exists but asset listing service (`apps.assets`) does not fan-out to followers. No notification hook in `Asset.create` path. |
| 2 | New Seller notification to platform followers | ❌ Missing | No `new_seller` kind. Unclear who "followers" are for a newly registered seller — no platform-level subscription model exists. |
| 3 | Auction Reminder (1hr + 10min) | ❌ Missing | No `auction_reminder` kind. No Celery tasks. No concept of "interested parties" (closest proxy is bidder history). Requires async scheduler. |
| 4 | Auction Win | ✅ Partial | `auction_won` kind exists in `apps.auctions.services` (lines 854–860, 924–930). Winner is notified. |
| 5 | Deposit Payment Request (60min after win) | ❌ Missing | No `deposit_reminder` kind. `auction_won` fires immediately with no follow-up task after 60min. Requires Celery `apply_async(countdown=3600)`. |
| 6 | Win Cancellation (winner + seller) | ❌ Missing | No `win_cancelled` kind. Payment failure path in `apps.payments` fires a `PAYMENT` notification to the buyer (line 464) but does not notify the seller and does not have a distinct cancellation kind. |
| 7 | Points Congratulations | ❌ Missing | No dedicated points notification. `new_badge` fires on badge award only. `PointService.earn_points()` (apps.loyalty) does not call `NotificationService`. |
| 8 | Pedigree Reminder | ❌ Missing | No `pedigree_reminder` kind. No concept of "pedigree-interested" users. No configurable timing mechanism. |

**Summary: 1 full match (`auction_won`), 6 fully missing, 1 partial (`win_cancellation` — buyer notified but not seller + no distinct kind).**

### 16.2 — Notification Customization

| # | Requirement | Status | Gap |
|---|-------------|--------|-----|
| A | Per-user per-kind enable/disable | ❌ Missing | No `NotificationPreference` model. All notifications are fired unconditionally. |
| B | Seller/buyer mode per notification type | ❌ Missing | No mode preference — `Notification.profile_type` field stores `"All"/"Customer"/"Seller"` but it is set by the sender, not chosen by the recipient. |
| C | Do Not Disturb time window | ❌ Missing | No DND model or service. No suppression logic in `NotificationService.create()`. |

---

## Required Backend Changes

### 1. New `NotificationKind` choices (additive migration)

Add to `apps.core.models.NotificationKind`:

```python
NEW_BIRD           = "new_bird",            "New bird listed"
NEW_SELLER         = "new_seller",          "New seller joined"
AUCTION_REMINDER   = "auction_reminder",    "Auction ending soon"
DEPOSIT_REMINDER   = "deposit_reminder",    "Deposit payment due"
WIN_CANCELLED      = "win_cancelled",       "Auction win cancelled"
POINTS_EARNED      = "points_earned",       "Points earned"
PEDIGREE_REMINDER  = "pedigree_reminder",   "Pedigree reminder"
```

Existing `new_badge` and `payment` and `auction_won` retained. **No migration needed** — `kind` is a `CharField` with `choices`; adding choices is metadata only.

---

### 2. `NotificationPreference` model — `apps.core.models`

```python
class NotificationPreference(BaseModel):
    profile     = models.OneToOneField("users.Profile", on_delete=models.CASCADE, related_name="notification_preference")
    enabled_kinds = models.JSONField(default=list)   # list of NotificationKind values; empty = all enabled
    dnd_start   = models.TimeField(null=True, blank=True)   # e.g. 22:00
    dnd_end     = models.TimeField(null=True, blank=True)   # e.g. 07:00
    seller_mode = models.BooleanField(default=False)  # prefer seller-scoped kinds
```

Migration required: `apps.core`.

**Service update:** `NotificationService.create()` should check `NotificationPreference` before inserting:
- If `enabled_kinds` is set and the kind is not in the list → skip
- If current server time (user's timezone) falls within DND window → skip (or queue for after DND)

---

### 3. Fan-out for New Bird (`new_bird`) — `apps.assets.services` (or signals)

When a `BirdAsset` (or any asset) is published by a seller, query `SellerFollow.objects.filter(seller=asset.owner, is_active=True)` and call `NotificationService.create()` for each follower's `CustomerProfile`.

Hook location: `AssetService.create()` or Django `post_save` signal on `Asset`.

---

### 4. Points earned notification — `apps.loyalty.services`

In `PointService.earn_points()`, after the `PointTransaction` is created, call:

```python
NotificationService.create(
    profile=profile,
    kind=NotificationKind.POINTS_EARNED,
    title="...",
    metadata={"amount": amount, "reason": reason},
)
```

---

### 5. Win Cancellation notification — `apps.payments.services`

When a `PaymentRequest` is rejected / expires and the auction item is released:
- Notify winner with `WIN_CANCELLED` kind
- Notify seller with `WIN_CANCELLED` kind

Hook: payment rejection / timeout handler in `apps.payments.services`.

---

### 6. Scheduled notifications (Celery) — requires new infrastructure

The following require async task scheduling — **Celery + Django-Celery-Beat must be added before these can be implemented**:

| Notification | Trigger | Delay |
|---|---|---|
| Auction Reminder | Auction creation / end_time set | `eta = end_time - 1hr` and `eta = end_time - 10min` |
| Deposit Payment Request | Auction win event | `countdown = 3600` (60min) |
| Pedigree Reminder | Custom — configurable per pedigree document | User-configured time |

**Celery setup cost:** `celery`, `django-celery-beat`, Redis/RabbitMQ broker — this is non-trivial infrastructure.  
Until Celery is added, these three notification types **cannot be implemented**.

---

### 7. New Seller notification (`new_seller`)

Requires defining who receives this notification. Options:
- **Option A:** Every existing customer receives it (broadcast) — expensive, low value
- **Option B:** Only customers who have ever followed at least one seller get it — niche
- **Option C:** Skip or defer — no clear subscriber list exists

Recommendation: **defer** until a platform-level subscription concept is defined.

---

### 8. Pedigree Reminder (`pedigree_reminder`)

No "pedigree-interested users" concept exists. The `apps.pedigrees` module has no follower/subscription model. This requires a new `PedigreeWatch` model linking profiles to pedigree documents with a `remind_at` field. **Defer until Pedigree module is more mature.**

---

## Notification Preference API

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET` | `/api/notifications/preferences/` | Customer / Seller | Get own preference (auto-create if missing) |
| `PATCH` | `/api/notifications/preferences/` | Customer / Seller | Update `enabled_kinds`, `dnd_start`, `dnd_end`, `seller_mode` |

---

## Build Order Recommendation

| Priority | Item | Effort | Blocker |
|----------|------|--------|---------|
| 1 | Add new `NotificationKind` values | Trivial | None |
| 2 | `NotificationPreference` model + migration | Low | None |
| 3 | Preference CRUD endpoints | Low | Needs model |
| 4 | `new_bird` fan-out hook in asset service | Low | Needs kind |
| 5 | `points_earned` notification in loyalty service | Low | Needs kind |
| 6 | `win_cancelled` notifications in payment service | Low-Medium | Needs kind |
| 7 | Celery setup (broker + beat) | High | Infrastructure decision |
| 8 | Auction reminder tasks (1hr + 10min) | Medium | Needs Celery |
| 9 | Deposit reminder task (60min post-win) | Low-Medium | Needs Celery |
| 10 | `new_seller` broadcast | Medium | Needs subscriber definition |
| 11 | `pedigree_reminder` | High | Needs PedigreeWatch model + Celery |

---

## Post-Implementation Notes

_(Fill after each item is implemented)_

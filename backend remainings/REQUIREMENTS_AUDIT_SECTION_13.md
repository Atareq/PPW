# Requirements Audit — Section 13 (Lucky Wheel & Million Challenge)

> Section: 13 — The Lucky Wheel (عجلة الحظ) + Million Challenge (تحدي المليون)
> Audit date: 2026-06-20
> Status: Not started — `apps.wheel` does not exist; all prize types are missing

---

## Extracted Requirements

### 13.2 — The Lucky Wheel (عجلة الحظ)

#### Activation Mechanism

| User Type | Trigger | Frequency |
|-----------|---------|-----------|
| Seller (بائع) | After completing a paid auction sale | Once per qualifying auction |
| Customer (مشتري) | After participating in any auction | Once per auction participated |

#### Wheel Icon (Mobile UI)

- Small icon on the left side of the screen
- Appears only when the user has an unused spin token
- Spins with sound effects

---

#### Customer Wheel — 8 Slots (عجلة المشتري)

| # | Prize (AR) | Prize (EN) | Notes |
|---|-----------|-----------|-------|
| 1 | 50 نقطة | 50 Points | Add to wallet |
| 2 | 100 نقطة | 100 Points | Add to wallet |
| 3 | شحن أول طير شراء مجاني | Free first bird shipping | Discount on next bird order shipping fee |
| 4 | خصم 10% اشتراك لوفت | 10% loft subscription discount | Discount on next package purchase |
| 5 | حظ أوفر المرة القادمة | Better luck next time | Empty — no prize |
| 6 | مزايدة بلون ذهبي (أسبوع) | Golden bid color for 1 week | Bids display in gold color to other users |
| 7 | مزايدة صامتة: إخفاء الهوية في مزاد واحد | Silent auction: anonymous bid in 1 auction | Appears as "مزايد مجهول" (unknown bidder) |
| 8 | تثبيت تعليق: تثبيت رسالة في أي شات لمدة دقيقة | Pin message in any chat room for 1 minute | — |

---

#### Seller Wheel — 8 Slots (عجلة البائع)

| # | Prize (AR) | Prize (EN) | Notes |
|---|-----------|-----------|-------|
| 1 | 50 نقطة | 50 Points | Add to wallet |
| 2 | 100 نقطة | 100 Points | Add to wallet |
| 3 | خصم عمولة التطبيق من مزادك القادم | Commission discount on next auction | Reduce platform fee on 1 auction |
| 4 | خصم 10% اشتراك لوفت | 10% loft subscription discount | Discount on next package purchase |
| 5 | حظ أوفر المرة القادمة | Better luck next time | Empty — no prize |
| 6 | خلفية غرفة مميزة (24 ساعة) | Featured room background for 24 hours | Auction room gets a special visual background |
| 7 | رسالة ترحيب آلية (بوت مجاني - يوم كامل) | Auto welcome bot message for 1 day | Automated greeting to users who enter the auction room |
| 8 | تنبيه العودة: إرسال تنبيه لمن غادر الغرفة | Return alert: notify users who left the room | Sends "المزاد وصل للحسم، عد الآن!" to all who left |

---

### 13.3 — Million Challenge (تحدي المليون) — Example of a Major Prize

#### Dedicated Screen

| Element | Description |
|---------|-------------|
| Countdown timer | Large countdown to challenge deadline |
| Top 10 leaderboard | Live ranking of top 10 participants |
| Personal progress bar | Shows current user's progress toward the prize |
| Floating Share Button | Social share of the challenge |

---

## Current Backend State

- No `apps.wheel` app — does not exist
- No `WheelSegment`, `WheelSpin`, or `SpinToken` models anywhere
- No spin activation trigger in `apps.auctions.services` (no hook on paid sale or bid placement)
- No prize delivery infrastructure for any of the 8 slot types beyond plain points
- No `Challenge` or `Leaderboard` model for 13.3

**Existing building blocks that CAN be reused:**
- `PointService.earn_points()` in `apps.loyalty` — handles points prizes (slots 1 & 2)
- `apps.packages` — subscription packages exist; discount hook can be added
- `apps.chat` — chat models exist; pinned message needs new field
- `apps.auctions` — bid model exists; anonymous flag and gold status need new fields
- `apps.core.NotificationService` — return alert notification can use this

---

## Gap Analysis

### Core Wheel Infrastructure

| Component | Status | Gap |
|-----------|--------|-----|
| `apps.wheel` app | ❌ Missing | Entire app needs to be created |
| `WheelSegment` model | ❌ Missing | label, prize_type, prize_value, probability_weight, target (customer/seller), is_active |
| `WheelSpin` model | ❌ Missing | profile FK, segment FK (result), source_auction FK, spun_at, prize_claimed |
| `SpinToken` model | ❌ Missing | profile FK, source (auction_id + role), created, used_at (nullable) |
| Spin activation — seller | ❌ Missing | No hook in `apps.auctions.services` on paid auction close |
| Spin activation — customer | ❌ Missing | No hook on bid placement / auction participation |
| Spin endpoint | ❌ Missing | `POST /api/wheel/spin/` — consumes token, selects segment by weight, delivers prize |
| Admin segment management | ❌ Missing | `GET/POST/PATCH/DELETE /api/wheel/segments/` |
| Spin history | ❌ Missing | `GET /api/wheel/my-spins/` |

---

### Prize Type Gaps

#### Points Prizes (slots 1 & 2 — both wheels)

| Status | Note |
|--------|------|
| ✅ Buildable | `PointService.earn_points()` exists — wheel service calls it with `reason="wheel_spin"` |

#### Free First Bird Shipping (customer slot 3)

| Status | Note |
|--------|------|
| ❌ Missing | No shipping fee model exists anywhere. `OrderItem` / `Order` has no `shipping_fee` field. Requires either a `ShippingDiscount` perk model or a shipping fee field added to `Order`. |

#### 10% Subscription Discount (customer slot 4, seller slot 4)

| Status | Note |
|--------|------|
| ❌ Missing | `apps.packages.SellerPackage` has no discount mechanism. Need a `SpinPerk` row of type `subscription_discount_10` checked in package activation path. |

#### Better Luck Next Time (customer slot 5, seller slot 5)

| Status | Note |
|--------|------|
| ✅ No action | Empty prize — just a `WheelSegment` with `prize_type = "empty"`, spin recorded, no perk created. |

#### Golden Bid Color — 1 Week (customer slot 6)

| Status | Note |
|--------|------|
| ❌ Missing | `apps.auctions.Bid` has no appearance/style fields. Need `is_golden = BooleanField` + `golden_until = DateTimeField` on `Bid` (or on `CustomerProfile`). Bid serializer must expose this flag so mobile can render gold. |

#### Silent / Anonymous Bid (customer slot 7)

| Status | Note |
|--------|------|
| ❌ Missing | `Bid` model always exposes `bidder` FK. Need: `anonymous_spin_perk` flag on `CustomerProfile` (consumed once) → bid serializer hides bidder name when flag is set, replacing with "مزايد مجهول". |

#### Pin Message in Chat — 1 Minute (customer slot 8)

| Status | Note |
|--------|------|
| ❌ Missing | No pinned message concept in `apps.chat`. Need `is_pinned` + `pinned_until` on `ChatMessage`. Service must auto-unpin after 60 seconds (requires Celery or a read-time check). |

#### Commission Discount on Next Auction (seller slot 3)

| Status | Note |
|--------|------|
| ❌ Missing | Commission is calculated in `apps.payments` approval path. No per-seller commission discount flag. Need `commission_discount_perk` on `SellerProfile` (one-use, consumed on next auction settlement). |

#### Featured Room Background — 24h (seller slot 6)

| Status | Note |
|--------|------|
| ❌ Missing | `Auction` model has no `room_background` or `is_featured_room` field. Need `featured_bg_until = DateTimeField(null=True)` on `Auction`. Mobile reads this to apply special background. |

#### Auto Welcome Bot Message — 1 Day (seller slot 7)

| Status | Note |
|--------|------|
| ❌ Missing | No bot/automated message infrastructure in `apps.chat`. Need: `welcome_bot_until = DateTimeField(null=True)` on `SellerProfile` or `Auction`; when a user joins the chat and the flag is active, the system creates a `ChatMessage` from a bot sender. Requires either Celery or a join-event hook. |

#### Return Alert Notification (seller slot 8)

| Status | Note |
|--------|------|
| ❌ Missing | No "user left room" tracking in chat. Need: track `ChatRoomLeave` events (or `last_seen_at` per user per room) so the seller can trigger a push to all who left. Uses `NotificationService` with a new `auction_return_alert` kind. No Celery needed if triggered manually by the seller. |

---

### Section 13.3 — Million Challenge

| Component | Status | Gap |
|-----------|--------|-----|
| `Challenge` model | ❌ Missing | title, start_date, end_date, goal_type, goal_value, prize_description |
| `ChallengeEntry` model | ❌ Missing | profile FK, challenge FK, progress_value, rank, updated_at |
| Leaderboard endpoint | ❌ Missing | `GET /api/challenges/<id>/leaderboard/` — top 10 + caller rank |
| Progress endpoint | ❌ Missing | `GET /api/challenges/<id>/my-progress/` |
| Admin management | ❌ Missing | `GET/POST/PATCH /api/challenges/admin/` |
| Countdown mechanism | ❌ Missing | Derived from `challenge.end_date` — no special model needed, just expose `end_date` |
| Share payload | ❌ Missing | `GET /api/challenges/<id>/share/` returns shareable text/link — no model needed |

---

## Required New Models Summary

### `apps.wheel` (new app)

| Model | Key Fields |
|-------|-----------|
| `WheelSegment` | label, prize_type (choices below), prize_value, probability_weight, target_profile (`customer`/`seller`/`both`), is_active, display_color |
| `SpinToken` | profile FK, source_type (`auction_sale`/`auction_bid`), source_id, used_at (nullable) |
| `WheelSpin` | profile FK, segment FK, token FK, spun_at, prize_claimed_at |
| `SpinPerk` | profile FK, perk_type (choices below), expires_at, is_used, used_at |

**`WheelPrizeType` choices:**
`points`, `shipping_discount`, `subscription_discount`, `golden_bid`, `anonymous_bid`, `pin_message`, `commission_discount`, `featured_room_bg`, `welcome_bot`, `return_alert`, `empty`

### Cross-app model changes

| App | Model | Change |
|-----|-------|--------|
| `apps.auctions` | `Bid` | Add `is_golden: BooleanField`, `golden_until: DateTimeField(null)` |
| `apps.auctions` | `Auction` | Add `featured_bg_until: DateTimeField(null)`, `welcome_bot_until: DateTimeField(null)` |
| `apps.auctions` | `Bid` | Add `is_anonymous: BooleanField(default=False)` |
| `apps.chat` | `ChatMessage` | Add `is_pinned: BooleanField`, `pinned_until: DateTimeField(null)` |
| `apps.core` | `NotificationKind` | Add `auction_return_alert` |

---

## API Surface (New Endpoints)

### Wheel endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET` | `/api/wheel/tokens/` | Customer / Seller | List unused spin tokens |
| `POST` | `/api/wheel/spin/` | Customer / Seller | Consume token, return segment result + perk |
| `GET` | `/api/wheel/my-spins/` | Customer / Seller | Spin history |
| `GET` | `/api/wheel/my-perks/` | Customer / Seller | Active perks with expiry |
| `GET/POST` | `/api/wheel/segments/` | Admin+ | List / create segments |
| `GET/PATCH/DELETE` | `/api/wheel/segments/<id>/` | Admin+ | Manage segment |

### Challenge endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET` | `/api/challenges/active/` | Public | Current active challenge |
| `GET` | `/api/challenges/<id>/leaderboard/` | Any | Top 10 + caller's rank |
| `GET` | `/api/challenges/<id>/my-progress/` | Customer / Seller | Caller's progress |
| `GET/POST` | `/api/challenges/admin/` | Admin+ | List / create |
| `PATCH` | `/api/challenges/admin/<id>/` | Admin+ | Update |

---

## Celery Dependency

| Feature | Needs Celery? | Note |
|---------|--------------|------|
| Pin message auto-unpin (1 min) | ✅ Yes | Or mobile reads `pinned_until` and ignores if expired |
| Auto welcome bot auto-disable | Soft no | `welcome_bot_until` checked on each join event |
| Featured room background expiry | Soft no | Mobile reads `featured_bg_until` and ignores if expired |
| Return alert notification | ❌ No | Seller manually triggers push |
| Golden bid expiry | Soft no | Bid serializer checks `golden_until < now()` and renders normal |

Most expiry behaviors can be handled read-side (mobile/API checks the `_until` field) — Celery is optional for cleanup but not required for correctness.

---

## Build Order Recommendation

| Priority | Item | Effort | Blocker |
|----------|------|--------|---------|
| 1 | `apps.wheel` + models (`WheelSegment`, `SpinToken`, `WheelSpin`, `SpinPerk`) | Medium | None |
| 2 | Spin activation hooks (auction close + bid placed) | Low | Needs models |
| 3 | `POST /api/wheel/spin/` — point & empty prizes only first | Low | Needs models |
| 4 | Golden bid + anonymous bid (model changes + serializer) | Medium | Needs `SpinPerk` |
| 5 | Subscription discount + commission discount perks | Medium | Needs `SpinPerk` |
| 6 | Featured room background + welcome bot flag | Low | Needs model fields |
| 7 | Return alert notification | Low | Needs `NotificationKind` |
| 8 | Pin message in chat | Medium | Needs chat model change |
| 9 | Free shipping perk | Medium | Needs shipping fee model (large scope) |
| 10 | Challenge model + leaderboard endpoints | Medium | None |
| 11 | Admin segment & challenge management dashboard pages | Medium | Needs endpoints |

---

## Post-Implementation Notes

_(Fill after each item is implemented)_

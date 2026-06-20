# PPW — Requirements Audit

> Each section: extracted requirements → backend gap table → post-implementation notes.
> Backend gaps only. Flutter/UI gaps are discussed in chat.

---

## Section 6 — Incentives & Rewards System (نظام المحفزات والجوائز)

### 6.1 Onboarding / Welcome Rewards (جوائز ترحيبية)

| # | Requirement | Backend Status | Notes |
|---|-------------|----------------|-------|
| 6.1.1 | 100 free points awarded on registration | ❌ Missing | `PointService.earn_points()` exists but is never called in registration flow. No hook in `apps.authentication.services`. |
| 6.1.2 | 20% discount on first auction highlight | ❌ Missing | No "highlight" concept on `Auction` model. No first-auction check exists anywhere. |
| 6.1.3 | Automatic weekly draw entry on registration | ❌ Missing | No draw/raffle model or Celery task exists. |

---

### 6.2 Interactive / Shared Rewards (جوائز تفاعلية مشتركة)

| # | Requirement | Backend Status | Notes |
|---|-------------|----------------|-------|
| 6.2.1 | Double points for both parties when seller + buyer complete a deal | ❌ Missing | No hook in `PaymentRequest` approval service. `PointService` exists but is unused here. |
| 6.2.2 | 50 points per bid in an active auction with 5+ bidders | ❌ Missing | No hook in `BidService` or bid create action. |
| 6.2.3 | 20 points for each party on a mutual review/rating | ❌ Missing | No hook in rating creation flow (`apps.ratings`). |

---

### 6.3 Seasonal Seller Rewards (جوائز البائعين الموسمية)

Admin sets per-season prizes. Winners ranked by weighted scoring:
- Completed sales: 40%
- Buyer ratings: 30%
- No-cancellation rate: 20%
- Buyer interaction: 10%

Prize tiers (admin-configurable):
- 1st: 100,000 points + Golden Cup badge
- 2nd: 50,000 points + Silver Cup badge
- 3rd: 25,000 points + Bronze Cup badge

| # | Requirement | Backend Status | Notes |
|---|-------------|----------------|-------|
| 6.3.1 | Weighted seller scoring service | ❌ Missing | No scoring computation exists. |
| 6.3.2 | Seasonal prize campaign model | ❌ Missing | `PrizeCampaign` model not yet built (noted in `REMAINING_BACKEND.md` §3). |
| 6.3.3 | Golden Cup / Silver Cup / Bronze Cup badge types | ❌ Missing | Only 3 badge types exist: `first_order`, `points_starter`, `seller_starter`. |
| 6.3.4 | Admin endpoint to trigger seasonal award | ❌ Missing | No campaign award endpoint. |

---

### 6.4 Monthly Referral Challenge (جوائز التحدي الشهري للمشاركة)

Concept: user who shares the most referral links in a month wins. New referred members accepted manually by admin to prevent bots.

Prize tiers: 1st → iPhone, 2nd → AirPods, 3rd → annual subscription.

Challenge UI needs: countdown timer (days:hours:minutes), top-10 leaderboard, personal progress bar showing points + gap to 1st place.

| # | Requirement | Backend Status | Notes |
|---|-------------|----------------|-------|
| 6.4.1 | Referral link generation per user | ❌ Missing | No referral model, no unique link generation. |
| 6.4.2 | Referral tracking (who referred whom) | ❌ Missing | No `Referral` model. |
| 6.4.3 | Manual admin approval of referred members | ❌ Missing | No approval flow for referral sign-ups. |
| 6.4.4 | Monthly challenge model with start/end dates | ❌ Missing | No `ReferralChallenge` model. |
| 6.4.5 | Referral count leaderboard endpoint (top 10) | ❌ Missing | No leaderboard endpoint anywhere. |
| 6.4.6 | Personal challenge progress endpoint | ❌ Missing | |
| 6.4.7 | Physical prize (iPhone/AirPods) fulfilment tracking | ❌ Missing | See §6.5. |

---

### 6.5 Prize Claim Workflow (طريقة استلام الجوائز)

- **Digital prizes** (points, cashback): auto-credited to account at award time.
- **Physical/cash prizes**: notification → winner fills claim form (name + address + national ID) → admin reviews → prize delivered.

| # | Requirement | Backend Status | Notes |
|---|-------------|----------------|-------|
| 6.5.1 | Digital prize auto-credit | ⚠️ Partial | `PointService.earn_points()` exists; no automatic trigger from campaign win. |
| 6.5.2 | Winner notification | ❌ Missing | `NotificationService` exists but no winner event type. |
| 6.5.3 | `PrizeClaim` model (name, address, national_id, status) | ❌ Missing | |
| 6.5.4 | Claim submission endpoint (winner POSTs form) | ❌ Missing | |
| 6.5.5 | Admin review + fulfil endpoints | ❌ Missing | |

---

### 6.6 PP Coins — In-App Point Purchase (شراء النقاط)

Users can buy additional PP Coins (points) directly from the app using credit cards or e-wallets. Points are used for: exclusive draw entries, discounts, premium service activation.

| # | Requirement | Backend Status | Notes |
|---|-------------|----------------|-------|
| 6.6.1 | Point purchase order model | ❌ Missing | No model for in-app point purchase. |
| 6.6.2 | Payment gateway integration for point purchase | ❌ Missing | `apps.payments` handles seller payment requests, not in-app purchases. |
| 6.6.3 | Point credit on successful payment confirmation | ❌ Missing | |
| 6.6.4 | Point-gated feature enforcement (exclusive draws) | ❌ Missing | No spending gate on any draw/raffle entry. |

---

### 6.7 Badge System — Paid & Threshold Badges (نظام الأوسمة)

Some distinctive badges (e.g. "Elite", "Golden") are either:
- **Purchasable** — user pays points or money to acquire
- **Threshold-unlocked** — auto-granted when a point/activity threshold is crossed (not manually granted)

| # | Requirement | Backend Status | Notes |
|---|-------------|----------------|-------|
| 6.7.1 | Premium/purchasable badge types (Elite, Golden, etc.) | ❌ Missing | `BadgeType` only has `first_order`, `points_starter`, `seller_starter`. |
| 6.7.2 | Badge purchase via points spend | ❌ Missing | No purchase flow; `BadgeService.award_badge()` is admin-callable only. |
| 6.7.3 | Auto-award on point/activity threshold crossing | ⚠️ Partial | `BadgeService.evaluate_badges()` exists but returns `[]` — no logic implemented. |
| 6.7.4 | Badge catalog endpoint (shows price/threshold per badge) | ⚠️ Partial | `BadgeService.get_badge_catalog()` returns catalog but no price/threshold fields on `BADGE_DEFINITIONS`. |

---

### 6.8 Marketing Offers (العروض التسويقية)

All offers created/managed from admin dashboard.

| Offer Category | Description |
|----------------|-------------|
| Monthly | Limited discounts on sale packages or highlights for a one-month period |
| Seasonal / Annual | Major promotions tied to seasons (wedding season, racing season) — discounts on subscriptions or double points |
| Flash Sales | 24–48 hour quick offers on specific services (e.g. 50% off first paid auction) |

| # | Requirement | Backend Status | Notes |
|---|-------------|----------------|-------|
| 6.8.1 | `start_date` / `end_date` on offers | ❌ Missing | `DiscountOffer` and `CashbackOffer` have no time bounds. `is_active` is the only control. |
| 6.8.2 | Offer category (`monthly`, `seasonal`, `flash`) | ❌ Missing | No category field on any offer model. |
| 6.8.3 | Auto-deactivate offer after `end_date` | ❌ Missing | No Celery task for offer expiry. |
| 6.8.4 | Flash sale (24–48 h) with countdown | ❌ Missing | No time-limited offer type. |
| 6.8.5 | Double-points offer type (points multiplier) | ❌ Missing | Already noted in `REMAINING_BACKEND.md` §7. |
| 6.8.6 | Subscription/package discount type | ⚠️ Partial | `DiscountOffer` applies to `OrderItem` assets; no concept of package/subscription discounting. |

---

## Gap Summary — Section 6

| Sub-section | Total Requirements | Implemented | Partial | Missing |
|-------------|-------------------|-------------|---------|---------|
| 6.1 Onboarding | 3 | 0 | 0 | 3 |
| 6.2 Interactive | 3 | 0 | 0 | 3 |
| 6.3 Seasonal | 4 | 0 | 0 | 4 |
| 6.4 Referral Challenge | 7 | 0 | 0 | 7 |
| 6.5 Prize Claims | 5 | 0 | 1 | 4 |
| 6.6 PP Coins Purchase | 4 | 0 | 0 | 4 |
| 6.7 Paid Badges | 4 | 0 | 2 | 2 |
| 6.8 Marketing Offers | 6 | 0 | 1 | 5 |
| **Total** | **36** | **0** | **4** | **32** |

---

### What Does Exist (Foundation)

- `PointWallet` + `PointTransaction` + `PointService` — ledger infrastructure, idempotent, atomic
- `BadgeAward` + `BadgeService.award_badge()` — award infrastructure
- `DiscountOffer` + `CashbackOffer` + `CashbackTransaction` — marketplace promo infrastructure
- `NotificationService` — notification delivery infrastructure
- `PointService.earn_points()` — ready to be called from any hook; just needs wiring

---

*Post-Implementation Notes: (to be filled after approval + implementation)*

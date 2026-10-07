# PPW Rewards and Badges System Report

> **Canonical source of truth:** [`REWARDS_SOURCE_OF_TRUTH.md`](REWARDS_SOURCE_OF_TRUTH.md).
> This report describes the original MVP implementation. Where the two disagree,
> the source-of-truth file wins. Sections not re-verified there (for example the
> deferred/blocked badge lists) may predate later modules such as Lucky Wheel,
> referrals, and seasons.

## 1. Overview

This implementation adds an MVP rewards engine to the existing `apps.loyalty` foundation. It reuses the current user-level `PointWallet`, immutable `PointTransaction` ledger, and profile-level `BadgeAward` table. Reward execution is event-driven through `RewardEngine.handle_event(...)`.

The MVP implements immediate rewards for profile creation, bidding, first seller listing/auction actions, ratings, follows, and confirmed sale completion. It also adds Celery task entry points for aggregate trusted buyer/seller badges.

## 2. Current Payment Boundary

PPW does not process real money. Payment is manual outside the app. `PaymentRequest` records confirmation only.

Seller approval of a `PaymentRequest` finalizes the sale internally and records a `CompletedDeal` (BE-REW-04). Market `OrderItem` seller approval is not sale completion. Auction item sold/won is not payment confirmation. Sale-completion rewards are granted only from the recorded `CompletedDeal` (`deal_completed` event, BE-REW-05). Rejected requests do not grant positive sale rewards.

There is no payment gateway, proof upload, wallet, refund engine, shipping, handoff, receipt confirmation, ownership history, automatic rollback, or cashback reversal workflow.

## 3. Reward Source Of Truth

Trusted sale completion event:

```text
deal_completed   (emitted once when a CompletedDeal is recorded)
```

Auction sale rewards are sourced from seller approval of `AuctionPaymentRequest`. Market sale rewards are sourced from seller approval of `MarketPaymentRequest` after order-item completion, stock mutation, cashback earning, and ownership transfer logic have succeeded.

No sale-completion reward is granted on auction win, auction item sold, payment request creation/update/rejection, order creation, order-item seller approval, cashback redemption, cashback earning, complaint creation, shipping, handoff, or buyer receipt confirmation.

## 4. Sync Vs Async Decision Policy

Synchronous rewards are used for cheap, current-object events:

- account onboarding PP (once per `UserAccount`)
- welcome buyer/seller badges
- first bid
- last-minute bid
- first auction published
- first market item listed
- payment request approved
- first rating submitted
- first seller followed

Asynchronous Celery tasks are used for aggregate rewards:

- trusted buyer
- trusted seller

Deal completion schedules trusted buyer/seller aggregate checks with `transaction.on_commit`; they count `CompletedDeal` rows.

## 5. Data Model Changes

`BadgeAward` now supports:

- `expires_at`
- `revoked_at`
- `idempotency_key`

The previous database constraint of one active badge per profile/type was removed. Reward services now enforce one-time badges where required and allow temporary badge history. Expired badges remain as historical records.

`PointTransaction` already had source fields, metadata, and an optional unique idempotency key, so no point schema changes were needed.

## 6. Idempotency Strategy

Every reward grant uses a deterministic key:

```text
reward:{reward_code}:{event_type}:{source_type}:{source_id}:profile:{profile_id}
```

The same key is used independently for the point transaction and badge award when an event grants both (`RewardEngine._grant_points_and_badge`); badge-only and points-only rewards use the matching single helper.

Exception: the account onboarding reward is user-scoped and uses
`reward:onboarding:user:{user_id}` so it is paid once per `UserAccount`, not once
per profile (BE-REW-02). `PointService` locks the wallet with `select_for_update()` and returns an existing transaction when the same logical key is retried.

Repeated payment approval attempts do not duplicate stock, ownership transfer, points, or badges.

## 7. Badge Validity Strategy

A badge is active when:

- `revoked_at` is null
- `expires_at` is null or in the future

`BadgeService.get_active_badges()` hides expired badges. `/api/loyalty/badges/my/` returns active badges by default and supports `include_expired=true` for history.

Temporary implemented badges:

- `LAST_MINUTE_FIGHTER`: 7 days
- `TRUSTED_BUYER`: 90 days
- `TRUSTED_SELLER`: 90 days

## 8. Point Wallet Strategy

Loyalty points remain separate from cashback. Points use the existing user-level `PointWallet` and immutable `PointTransaction` ledger. Customer and seller modes of one `UserAccount` share that single wallet; `PointTransaction.profile` is context only. Points are earned through `PointService.earn_points()` only.

## 9. Cashback Separation Rule

Cashback is not loyalty points. Cashback remains in `apps.promotions` through `CustomerProfile.cashback_balance` and `CashbackTransaction`. Cashback redemption or earning does not create `PointTransaction` records by itself.

## 10. Event Mapping Table

| Event | Source | Timing | Implemented |
| --- | --- | --- | --- |
| `customer_profile_created` | `CustomerProfile` | Sync | Yes (welcome badge + account onboarding PP if not yet awarded) |
| `seller_profile_created` | `SellerProfile` | Sync | Yes (welcome badge + account onboarding PP if not yet awarded) |
| `auction_published` | `Auction` | Sync | Yes |
| `market_item_listed` | `Asset` product subtype | Sync | Yes |
| `bid_placed` | `Bid` | Sync | Yes |
| `deal_completed` | `CompletedDeal` (`auction` or `market`) | Sync + async aggregate enqueue | Yes (replaces the two payment-approved events, BE-REW-05). Since BE-REW-08 also pays both parties `floor(amount × rate) × 2` PP plus a 100 PP bonus (section 9), and since BE-REW-09 the seller a recurring 100 PP successful-sale reward (section 10) |
| `rating_created` | `AssetRating` / `SellerRating` | Sync | Yes |
| `mutual_rating_completed` | `DealRating` pair on one `CompletedDeal` | Sync | Yes (BE-REW-10): buyer and seller 20 PP each, points only; does not emit `rating_created` |
| `seller_followed` | `SellerFollow` | Sync + future aggregate | Yes |
| `payment_request_created` | `PaymentRequest` | None | Deferred |
| `payment_request_rejected` | `PaymentRequest` | None | Deferred |
| `order_item_approved` | `OrderItem` | None | Deferred |
| `complaint_created/resolved` | `Complaint` | None | Deferred |
| views/insights/message/package events | Existing app models | Async/future | Deferred |

## 11. Buyer Badge Stories

Since BE-REW-06 a badge never implies PP Coins. The points column shows what the same event pays alongside the badge; `RewardEngine` grants points and badges separately.

| Code | Role | Story | Trigger | Source model/event | Event points (paid by the event, not the badge) | Badge validity | Sync/Async | Status | Reason |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| `WELCOME_BUYER` | Buyer | New customer starter badge | `customer_profile_created` | `CustomerProfile` | 0 (onboarding PP is account-level, once per `UserAccount`; default 100 via the `ACCOUNT_ONBOARDING` reward rule) | Permanent | Sync | Implemented | Customer registration hook exists |
| `FIRST_BIDDER` | Buyer | First accepted bid | `bid_placed` | `Bid` | 10 | Permanent | Sync | Implemented | Bid service hook exists |
| `LAST_MINUTE_FIGHTER` | Buyer | Bid in final five minutes | `bid_placed` | `Bid` | 5 | 7 days | Sync | Implemented | Uses pre-auto-extend last-minute flag |
| (no badge) `ACTIVE_AUCTION_BIDDER` | Buyer | Auction reaches 5 distinct bidder accounts | `bid_placed` | `Bid` (auction level) | 50 per auction (rule-configurable) | n/a | Sync | Implemented (BE-REW-07) | Points only; see `REWARDS_SOURCE_OF_TRUTH.md` section 8 |
| `AUCTION_PURCHASE_CONFIRMED` | Buyer | Confirmed auction purchase | `deal_completed` (auction) | `CompletedDeal` | 50 | Permanent | Sync | Implemented | Seller approval is sale confirmation |
| `MARKET_PURCHASE_CONFIRMED` | Buyer | Confirmed market purchase | `deal_completed` (market) | `CompletedDeal` | 30 | Permanent | Sync | Implemented | Runs after market finalization succeeds |
| `REVIEWER_BUYER` | Buyer | First valid rating | `rating_created` | `AssetRating` / `SellerRating` | 10 | Permanent | Sync | Implemented | Rating services already validate completed sale |
| `LOYAL_FOLLOWER` | Buyer | First active seller follow | `seller_followed` | `SellerFollow` | 5 | Permanent | Sync | Implemented | Follow service hook exists |
| `TRUSTED_BUYER` | Buyer | Ten confirmed purchases | Aggregate | `CompletedDeal` count | 100 | 90 days | Async | Implemented | Celery task entry point added |
| `BIRD_COLLECTOR` | Buyer | Five confirmed bird purchases | Aggregate | Approved bird `PaymentRequest` count | 50 | 90 days | Async | Deferred | Not in MVP implementation scope |
| `ACTIVE_BIDDER` | Buyer | Twenty bids in 30 days | Aggregate | `Bid` count | 50 | 30 days | Async | Deferred | Periodic task not implemented |
| `AUCTION_WINNER_STREAK` | Buyer | Three auction confirmations in 30 days | Aggregate | Approved auction requests | 75 | 30 days | Async | Deferred | Periodic task not implemented |
| `MONTHLY_TOP_BUYER` | Buyer | Monthly top buyer | Leaderboard | Approved requests | Optional | 30 days | Async | Deferred | Leaderboard policy not implemented |

## 12. Seller Badge Stories

| Code | Role | Story | Trigger | Source model/event | Event points (paid by the event, not the badge) | Badge validity | Sync/Async | Status | Reason |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| `WELCOME_SELLER` | Seller | New seller starter badge | `seller_profile_created` | `SellerProfile` | 0 (onboarding PP is account-level, once per `UserAccount`; default 100 via the `ACCOUNT_ONBOARDING` reward rule) | Permanent | Sync | Implemented | Seller mobile profile hook exists |
| `FIRST_AUCTION_PUBLISHED` | Seller | First auction published | `auction_published` | `Auction` | 25 | Permanent | Sync | Implemented | Auction creation service hook exists |
| `FIRST_MARKET_ITEM_LISTED` | Seller | First market product listed | `market_item_listed` | Product `Asset` | 25 | Permanent | Sync | Implemented | Product creation service hook exists |
| `FIRST_AUCTION_SALE_CONFIRMED` | Seller | First confirmed auction sale | `deal_completed` (auction) | `CompletedDeal` | 75 | Permanent | Sync | Implemented | Seller approval of auction request |
| `FIRST_MARKET_SALE_CONFIRMED` | Seller | First confirmed market sale | `deal_completed` (market) | `CompletedDeal` | 50 | Permanent | Sync | Implemented | Seller approval after stock mutation |
| `TRUSTED_SELLER` | Seller | Ten confirmed sales | Aggregate | `CompletedDeal` count | 150 | 90 days | Async | Implemented | Celery task entry point added |
| `HIGH_RATED_SELLER` | Seller | Average rating >= 4.5 with 5 ratings | Aggregate | Seller rating fields | 100 | 90 days | Async | Deferred | Aggregate task not implemented |
| `POPULAR_SELLER` | Seller | High profile/item attention | Aggregate | `SellerInsightEvent` | 75 | 30 days | Async | Deferred | Threshold task not implemented |
| `FOLLOWED_SELLER` | Seller | 50 active followers | Aggregate | `SellerFollow` | 75 | 90 days | Async | Deferred | Aggregate task not implemented |
| `ACTIVE_SELLER` | Seller | Five auctions/listings in 30 days | Aggregate | `Auction` / product `Asset` | 50 | 30 days | Async | Deferred | Periodic task not implemented |
| `STOCK_MASTER` | Seller | Ten completed market sales | Aggregate | Approved market requests | 75 | 90 days | Async | Deferred | Periodic task not implemented |
| `FAST_RESPONDER` | Seller | Quick chat response | Aggregate | `Message` | 50 | 30 days | Async | Blocked | Response-time rule is not modeled safely |
| `CLEAN_SELLER` | Seller | Sales with no active complaints | Aggregate | `Complaint` + approved requests | 100 | 90 days | Async | Deferred | Complaints do not reverse rewards; positive aggregate only future |
| `MULTI_ROOM_SELLER` | Seller | Multiple seller rooms | Missing | Seller rooms | TBD | TBD | TBD | Blocked | Seller rooms do not exist |
| `ELITE_SELLER` | Seller | Combined high-level seller criteria | Aggregate | Sales/ratings/follows | 250 | 90 days | Async | Deferred | Composite policy not implemented |

## 13. Shared/Campaign Badge Stories

| Code | Role | Story | Trigger | Source model/event | Event points (paid by the event, not the badge) | Badge validity | Sync/Async | Status | Reason |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| `WEEKLY_PARTICIPANT` | Shared | Weekly activity recognition | Weekly aggregate | Bids/purchases/sales/listings | Optional | 7 days | Async | Deferred | Weekly scheduler not implemented |
| `MONTHLY_TOP_SELLER` | Seller | Monthly top seller | Monthly leaderboard | Approved requests | Optional | 30 days | Async | Deferred | Leaderboard policy not implemented |
| `MONTHLY_TOP_BUYER` | Buyer | Monthly top buyer | Monthly leaderboard | Approved requests | Optional | 30 days | Async | Deferred | Leaderboard policy not implemented |
| `REFERRAL_CHALLENGE` | Shared | Referral progress | Challenge finalization | `ReferralChallenge` / `ReferralChallengeWinner` (BE-REF-03) | None | TBD | Sync | Deferred | The monthly referral challenge exists since BE-REF-03 (approved-referral score, Top 10, Top 3 prize winners), records `CLAIM_REQUIRED` / `PENDING` `PrizeAward` rows for canonical in-kind winners (`IPHONE`, `AIRPODS`) in `apps.prizes` (BE-PRZ-01), and supports winner claim submission (`full_name`, `address`, `identity_number`) and manager review/delivery (`PrizeClaim`, BE-PRZ-02); it awards no badge and no PP |
| `SEASONAL_BADGE` | Shared | Admin seasonal campaigns | Missing | Campaign engine | TBD | TBD | TBD | Deferred | Campaign engine not requested; seller reward seasons (`apps.seasons`, BE-SEA-01/02/03) score sellers, pay configured Top-3 `SeasonPrize.pp_points` (default `100,000 / 50,000 / 25,000`) on finalization, and record `DIGITAL_AUTOMATIC` / `FULFILLED` `PrizeAward` rows in `apps.prizes` (BE-PRZ-01) without awarding badges or fulfilling physical trophies |
| `PAID_BADGE` | Shared | Buy a badge | Missing | In-app payment gateway | TBD | TBD | TBD | Blocked | No in-app payment gateway exists |

## 14. Implemented Rewards

- `WELCOME_BUYER`
- `WELCOME_SELLER`
- `FIRST_BIDDER`
- `LAST_MINUTE_FIGHTER`
- `AUCTION_PURCHASE_CONFIRMED`
- `MARKET_PURCHASE_CONFIRMED`
- `REVIEWER_BUYER`
- `LOYAL_FOLLOWER`
- `FIRST_AUCTION_PUBLISHED`
- `FIRST_MARKET_ITEM_LISTED`
- `FIRST_AUCTION_SALE_CONFIRMED`
- `FIRST_MARKET_SALE_CONFIRMED`
- `TRUSTED_BUYER`
- `TRUSTED_SELLER`

## 15. Deferred Rewards

- `BIRD_COLLECTOR`
- `ACTIVE_BIDDER`
- `AUCTION_WINNER_STREAK`
- `MONTHLY_TOP_BUYER`
- `HIGH_RATED_SELLER`
- `POPULAR_SELLER`
- `FOLLOWED_SELLER`
- `ACTIVE_SELLER`
- `STOCK_MASTER`
- `CLEAN_SELLER`
- `WEEKLY_PARTICIPANT`
- `MONTHLY_TOP_SELLER`
- `SEASONAL_BADGE`
- payment-request-created progress badges
- order-item-approval responsiveness badges
- complaint aggregate recalculation hooks
- shipping/receipt rewards

Shipping/receipt-based rewards are deferred because there is no shipping/handoff model.

## 16. Blocked Rewards

- `FAST_RESPONDER`: chat messages exist, but reliable first-response-time semantics are not modeled.
- `MULTI_ROOM_SELLER`: seller rooms do not exist.
- `REFERRAL_CHALLENGE`: the monthly challenge exists (BE-REF-03, see `REWARDS_SOURCE_OF_TRUTH.md` section 14), records `CLAIM_REQUIRED` / `PENDING` `PrizeAward` rows for `IPHONE` and `AIRPODS` winners (BE-PRZ-01, section 25), and supports winner claim submission and manager review/delivery via `PrizeClaim` (BE-PRZ-02, section 26), but awards no badge or PP.
- `PAID_BADGE`: paid badges are blocked because PPW has no in-app payment gateway.

## 17. Test Coverage

Added and updated tests cover:

- welcome buyer/seller one-time badges
- account onboarding PP once per `UserAccount` across customer/seller modes (`tests/loyalty/test_account_onboarding_reward.py`)
- first bid idempotency
- last-minute badge timing and expiry
- active badge filtering
- auction payment approval buyer/seller rewards
- market payment approval buyer/seller rewards
- no sale reward on request creation/rejection
- no sale reward on order-item seller approval alone
- no market reward when stock mutation fails
- repeated approval attempt does not duplicate rewards
- trusted buyer/seller aggregate task logic
- deferred unknown rules do not crash

Focused passing commands:

```bash
PYENV_VERSION=ppw-env pyenv exec pytest tests/loyalty
PYENV_VERSION=ppw-env pyenv exec pytest tests/payments
PYENV_VERSION=ppw-env pyenv exec python manage.py makemigrations --check --dry-run
```

## 18. Open Questions

- Should buyer purchase-confirmed points be awarded for every confirmed purchase, or only once per profile? The MVP awards points per approved `PaymentRequest` and keeps one profile badge per purchase-confirmed badge type.
- Should temporary aggregate badges be renewable with new points after expiry? The MVP supports temporary badge expiry and historical records, but trusted aggregate rewards are idempotent per profile/code.
- Should reward results be exposed in API responses or notifications? Congratulatory notifications for PP Coin earnings (`points_earned`), badge awards (`badge_awarded`), and claim-required prize wins (`prize_awarded`) are implemented in `BE-NOT-01` using `apps.notifications`.

## 19. Future Enhancements

- Add scheduled Celery Beat entries for aggregate recalculations.
- Add leaderboard reward policies.
- Add renewable temporary aggregate reward periods.
- Add notification grouping/digests and Do-Not-Disturb / per-category preferences (`BE-NOT-02`).
- Add safe response-time metrics before implementing `FAST_RESPONDER`.
- Scheduled promotion campaign types (`PromotionCampaign`: `MONTHLY`, `SEASONAL`, `FLASH`), `[starts_at, ends_at)` scheduling (BE-PRO-01), service-level `PERCENT_DISCOUNT` campaign effects (`PromotionCampaignEffect` + `PromotionPricingService.resolve_service_discount` integrated with Seller Packages and Loft Subscriptions, BE-PRO-02), and the account-level onboarding 20% first auction-highlight discount entitlement, campaign comparison quote, and trusted atomic consumption service (`OnboardingHighlightBenefitService`, BE-PRO-03; actual highlight purchase wiring deferred pending a canonical highlight domain) are implemented in `apps.promotions`; executable `POINTS_MULTIPLIER` campaign effects remain deferred pending canonical reward-event scope definition, and seasonal campaign badge automation remains separate.
- Weekly onboarding draw domain, automatic account-level entry, and post-`ends_at` participant-freeze foundation (`apps.draws`: `WeeklyDraw`, `WeeklyDrawEntry`, `WeeklyDrawService`, `WeeklyDrawFinalizationService`, `WeeklyDrawEntryService`, BE-DRW-01 & BE-DRW-02) are implemented and separated from the Lucky Wheel (`apps.lucky_wheel`); actual winner selection and transition to `FINALIZED` remain blocked pending product confirmation of winner count, selection algorithm, and prize definition.
- Lucky Wheel eligibility, attempt lifecycle, spin outcome selection, and prize execution (`apps.lucky_wheel`: `LuckyWheelService.get_availability`, `LuckyWheelGrantService`, `LuckyWheelPrizeSelector`, and `LuckyWheelSpinService`, BE-LW-01, BE-LW-01A & BE-LW-02) are hardened and separated from Weekly Onboarding Draws (`apps.draws`); `available`/`eligible` require both an active non-exhausted prize segment (`weight > 0`) and `remaining_attempts > 0`, usable grants are ordered by `expires_at ASC NULLS LAST, created ASC, id ASC`, attempt grants use a savepoint-guarded create with collision validation on `IntegrityError` recovery, weighted prize selection uses `secrets.SystemRandom()` behind injectable `LuckyWheelPrizeSelector.select()`, finite prize inventory uses `select_for_update()` with bounded stale-candidate re-resolution, `LuckyWheelPrize.clean()` enforces strict one-of reward fields and `quantity_total >= quantity_awarded`, spin-time offer/badge/point validation rolls back the entire transaction on failure, `prize_snapshot` preserves immutable prize/offer details, public endpoints strip internal `metadata` / `source_id` / `weight` / `idempotency_key` fields, and spin requests reject client-supplied outcome/prize fields.
- Shared prize award and fulfillment foundation and claim workflow (`apps.prizes`: `PrizeAward`, `PrizeClaim`, `PrizeAwardService`, `PrizeClaimService`, BE-PRZ-01 & BE-PRZ-02) record immutable `UserAccount`-owned prize awards across `LUCKY_WHEEL` (`DIGITAL_AUTOMATIC` / `FULFILLED` for `PP_COINS`, `DISCOUNT_OFFER`, `CASHBACK_OFFER`, and `BADGE`; `0` rows for `NO_PRIZE`), `SEASON` (`DIGITAL_AUTOMATIC` / `FULFILLED` for Top-3 `pp_points > 0` winners), and `REFERRAL_CHALLENGE` (`CLAIM_REQUIRED` / `PENDING` for canonical in-kind `IPHONE` and `AIRPODS` winners) without double-executing rewards or storing claim PII on `PrizeAward`, and support winner claim submission (`full_name`, `address`, `identity_number` on `PrizeClaim`) and manager review/delivery (`SUBMITTED -> REVIEWED -> DELIVERED`, fulfilling `PrizeAward` on delivery).
- Reward and prize congratulatory notifications (`BE-NOT-01`) emit `points_earned` on newly created positive `PointTransaction` rows (`PointService._create_transaction`), `badge_awarded` on newly created `BadgeAward` rows (`BadgeService.award_badge`), and `prize_awarded` on newly created `CLAIM_REQUIRED` `PrizeAward` rows (`PrizeAwardService._record_award`, with `0` generic prize notifications on `record_digital_fulfilled`), reusing `NotificationService.create_notification` and post-commit WebSocket/FCM delivery; notification grouping/digests and DND preferences are deferred to `BE-NOT-02`.




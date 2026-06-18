# PPW Rewards and Badges System Report

## 1. Overview

This implementation adds an MVP rewards engine to the existing `apps.loyalty` foundation. It reuses the current user-level `PointWallet`, immutable `PointTransaction` ledger, and profile-level `BadgeAward` table. Reward execution is event-driven through `RewardEngine.handle_event(...)`.

The MVP implements immediate rewards for profile creation, bidding, first seller listing/auction actions, ratings, follows, and confirmed sale completion. It also adds Celery task entry points for aggregate trusted buyer/seller badges.

## 2. Current Payment Boundary

PPW does not process real money. Payment is manual outside the app. `PaymentRequest` records confirmation only.

Seller approval of a `PaymentRequest` finalizes the sale internally. Market `OrderItem` seller approval is not sale completion. Auction item sold/won is not payment confirmation. Sale-completion rewards are granted only on `PaymentRequest` approved. Rejected requests do not grant positive sale rewards.

There is no payment gateway, proof upload, wallet, refund engine, shipping, handoff, receipt confirmation, ownership history, automatic rollback, or cashback reversal workflow.

## 3. Reward Source Of Truth

Trusted sale completion event:

```text
payment_request_approved
```

Auction sale rewards are sourced from seller approval of `AuctionPaymentRequest`. Market sale rewards are sourced from seller approval of `MarketPaymentRequest` after order-item completion, stock mutation, cashback earning, and ownership transfer logic have succeeded.

No sale-completion reward is granted on auction win, auction item sold, payment request creation/update/rejection, order creation, order-item seller approval, cashback redemption, cashback earning, complaint creation, shipping, handoff, or buyer receipt confirmation.

## 4. Sync Vs Async Decision Policy

Synchronous rewards are used for cheap, current-object events:

- welcome buyer/seller
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

The payment approval flow schedules trusted buyer/seller aggregate checks with `transaction.on_commit`.

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

The same key is used independently for the point transaction and badge award. `PointService` locks the wallet with `select_for_update()` and returns an existing transaction when the same logical key is retried.

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

Loyalty points remain separate from cashback. Points use the existing user-level `PointWallet` and immutable `PointTransaction` ledger. Points are earned through `PointService.earn_points()` only.

## 9. Cashback Separation Rule

Cashback is not loyalty points. Cashback remains in `apps.promotions` through `CustomerProfile.cashback_balance` and `CashbackTransaction`. Cashback redemption or earning does not create `PointTransaction` records by itself.

## 10. Event Mapping Table

| Event | Source | Timing | Implemented |
| --- | --- | --- | --- |
| `customer_profile_created` | `CustomerProfile` | Sync | Yes |
| `seller_profile_created` | `SellerProfile` | Sync | Yes |
| `auction_published` | `Auction` | Sync | Yes |
| `market_item_listed` | `Asset` product subtype | Sync | Yes |
| `bid_placed` | `Bid` | Sync | Yes |
| `auction_payment_request_approved` | `AuctionPaymentRequest` | Sync + async aggregate enqueue | Yes |
| `market_payment_request_approved` | `MarketPaymentRequest` | Sync + async aggregate enqueue | Yes |
| `rating_created` | `AssetRating` / `SellerRating` | Sync | Yes |
| `seller_followed` | `SellerFollow` | Sync + future aggregate | Yes |
| `payment_request_created` | `PaymentRequest` | None | Deferred |
| `payment_request_rejected` | `PaymentRequest` | None | Deferred |
| `order_item_approved` | `OrderItem` | None | Deferred |
| `complaint_created/resolved` | `Complaint` | None | Deferred |
| views/insights/message/package events | Existing app models | Async/future | Deferred |

## 11. Buyer Badge Stories

| Code | Role | Story | Trigger | Source model/event | Points | Badge validity | Sync/Async | Status | Reason |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| `WELCOME_BUYER` | Buyer | New customer starter reward | `customer_profile_created` | `CustomerProfile` | 100 | Permanent | Sync | Implemented | Customer registration hook exists |
| `FIRST_BIDDER` | Buyer | First accepted bid | `bid_placed` | `Bid` | 10 | Permanent | Sync | Implemented | Bid service hook exists |
| `LAST_MINUTE_FIGHTER` | Buyer | Bid in final five minutes | `bid_placed` | `Bid` | 5 | 7 days | Sync | Implemented | Uses pre-auto-extend last-minute flag |
| `AUCTION_PURCHASE_CONFIRMED` | Buyer | Confirmed auction purchase | `auction_payment_request_approved` | `PaymentRequest` | 50 | Permanent | Sync | Implemented | Seller approval is sale confirmation |
| `MARKET_PURCHASE_CONFIRMED` | Buyer | Confirmed market purchase | `market_payment_request_approved` | `PaymentRequest` | 30 | Permanent | Sync | Implemented | Runs after market finalization succeeds |
| `REVIEWER_BUYER` | Buyer | First valid rating | `rating_created` | `AssetRating` / `SellerRating` | 10 | Permanent | Sync | Implemented | Rating services already validate completed sale |
| `LOYAL_FOLLOWER` | Buyer | First active seller follow | `seller_followed` | `SellerFollow` | 5 | Permanent | Sync | Implemented | Follow service hook exists |
| `TRUSTED_BUYER` | Buyer | Ten confirmed purchases | Aggregate | Approved `PaymentRequest` count | 100 | 90 days | Async | Implemented | Celery task entry point added |
| `BIRD_COLLECTOR` | Buyer | Five confirmed bird purchases | Aggregate | Approved bird `PaymentRequest` count | 50 | 90 days | Async | Deferred | Not in MVP implementation scope |
| `ACTIVE_BIDDER` | Buyer | Twenty bids in 30 days | Aggregate | `Bid` count | 50 | 30 days | Async | Deferred | Periodic task not implemented |
| `AUCTION_WINNER_STREAK` | Buyer | Three auction confirmations in 30 days | Aggregate | Approved auction requests | 75 | 30 days | Async | Deferred | Periodic task not implemented |
| `MONTHLY_TOP_BUYER` | Buyer | Monthly top buyer | Leaderboard | Approved requests | Optional | 30 days | Async | Deferred | Leaderboard policy not implemented |

## 12. Seller Badge Stories

| Code | Role | Story | Trigger | Source model/event | Points | Badge validity | Sync/Async | Status | Reason |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| `WELCOME_SELLER` | Seller | New seller starter reward | `seller_profile_created` | `SellerProfile` | 100 | Permanent | Sync | Implemented | Seller mobile profile hook exists |
| `FIRST_AUCTION_PUBLISHED` | Seller | First auction published | `auction_published` | `Auction` | 25 | Permanent | Sync | Implemented | Auction creation service hook exists |
| `FIRST_MARKET_ITEM_LISTED` | Seller | First market product listed | `market_item_listed` | Product `Asset` | 25 | Permanent | Sync | Implemented | Product creation service hook exists |
| `FIRST_AUCTION_SALE_CONFIRMED` | Seller | First confirmed auction sale | `auction_payment_request_approved` | `PaymentRequest` | 75 | Permanent | Sync | Implemented | Seller approval of auction request |
| `FIRST_MARKET_SALE_CONFIRMED` | Seller | First confirmed market sale | `market_payment_request_approved` | `PaymentRequest` | 50 | Permanent | Sync | Implemented | Seller approval after stock mutation |
| `TRUSTED_SELLER` | Seller | Ten confirmed sales | Aggregate | Approved `PaymentRequest` count | 150 | 90 days | Async | Implemented | Celery task entry point added |
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

| Code | Role | Story | Trigger | Source model/event | Points | Badge validity | Sync/Async | Status | Reason |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| `WEEKLY_PARTICIPANT` | Shared | Weekly activity recognition | Weekly aggregate | Bids/purchases/sales/listings | Optional | 7 days | Async | Deferred | Weekly scheduler not implemented |
| `MONTHLY_TOP_SELLER` | Seller | Monthly top seller | Monthly leaderboard | Approved requests | Optional | 30 days | Async | Deferred | Leaderboard policy not implemented |
| `MONTHLY_TOP_BUYER` | Buyer | Monthly top buyer | Monthly leaderboard | Approved requests | Optional | 30 days | Async | Deferred | Leaderboard policy not implemented |
| `REFERRAL_CHALLENGE` | Shared | Referral progress | Missing | Referral system | TBD | TBD | TBD | Blocked | Referral system does not exist |
| `SEASONAL_BADGE` | Shared | Admin seasonal campaigns | Missing | Campaign engine | TBD | TBD | TBD | Deferred | Campaign engine not requested |
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
- `REFERRAL_CHALLENGE`: referral system does not exist.
- `PAID_BADGE`: paid badges are blocked because PPW has no in-app payment gateway.

## 17. Test Coverage

Added and updated tests cover:

- welcome buyer/seller one-time rewards
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
- Should reward results be exposed in API responses or notifications? The MVP stores points and badges only.

## 19. Future Enhancements

- Add scheduled Celery Beat entries for aggregate recalculations.
- Add leaderboard reward policies.
- Add renewable temporary aggregate reward periods.
- Add notification/event delivery for awarded rewards.
- Add safe response-time metrics before implementing `FAST_RESPONDER`.
- Add admin campaign models before implementing seasonal badges.

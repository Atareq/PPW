# PPW Backend AGENTS Guide

## Purpose

This file is the main backend engineering reference for PPW.

It has two jobs:

- document the current codebase truth so feature work stays aligned with the repo
- document clearly labeled planned direction for the coming phases without pretending those parts already exist

When current implementation and future intent differ, current code is the source of truth for behavior until the planned work is implemented.

## Project Summary

PPW is a Django + DRF backend for a bird marketplace and auction platform.

The platform currently supports and will continue to expand around:

- user authentication with multiple profile types
- bird ownership and bird listing
- auction creation and bidding
- scheduled auction closing with winner resolution
- fixed-price product marketplace foundations through carts, checkout, orders, and seller item decisions
- ratings and comments as a simple trust and feedback layer
- race records and race standings for pigeon/bird racing reports
- planned admin-assisted post-auction and marketplace completion
- future media, pedigree, QR, reviews, points, chat, and promotion modules

PPW is a marketplace coordination system, not a fintech system and not a shipping-integrated platform.

Core business reality and intended product policy:

- no in-app payment gateway
- no automated shipping or logistics integration
- payment is manual and confirmed by admin
- handoff or delivery is manually confirmed by seller
- ownership transfer happens only after the required manual confirmations

Current implementation caveat:

- manual payment review, seller handoff confirmation, and ownership transfer workflows are not implemented yet

The project must remain a modular monolith. Simplicity and correctness are preferred over feature richness and abstractions.

## Technology Stack

- Python 3.11
- Django 4.2.15
- Django REST Framework 3.16.1
- drf-spectacular
- PostgreSQL
- Docker
- Docker Compose
- Redis
- Celery
- Gunicorn
- Nginx in production
- JWT authentication via SimpleJWT
- JWT profile synchronization via custom SimpleJWT authentication
- django-filter
- safedelete
- phonenumber_field
- drf-writable-nested

General approach:

- modular Django apps
- service / validator / factory pattern where useful
- explicit business flows
- no hidden magic
- avoid fat views and serializer-heavy business logic

Current internal API documentation approach:

- OpenAPI schema generation via drf-spectacular
- Swagger UI for local development
- targeted `@extend_schema` annotations only where automatic schema generation is weak

## Current Backend Architecture

### App Structure

Current major apps:

- `apps.authentication`
- `apps.users`
- `apps.assets`
- `apps.orders`
- `apps.auctions`
- `apps.ratings`
- `apps.races`
- `apps.general`

### Container Runtime / Deployment Shape

Current Docker setup:

- `Dockerfile` is a multi-stage build with a shared runtime image for web and worker roles
- `compose.yaml` is the shared base Compose file
- `compose.local.yaml` is the local development override
- `compose.prod.yaml` is the production override

Current service roles:

- `app`: Django web app served by Gunicorn
- `celery_worker`: background task execution
- `celery_beat`: scheduled task runner
- `redis`: Celery broker and result backend
- `db`: PostgreSQL service defined by the local and production Compose overrides
- `nginx`: production reverse proxy in production Docker only

Current container behavior:

- `docker/entrypoint.sh` waits for PostgreSQL before starting
- web role runs migrations and `collectstatic` before Gunicorn starts
- worker and beat roles reuse the same image and switch behavior through `APP_ROLE`
- local Docker uses `.env` and bind mounts the repo into `/app`
- local PostgreSQL is exposed on host port `5433` and container port `5432`
- production Docker uses `.env.prod` for Django, Celery, and the Compose-managed `db` service, and serves static files through Nginx
- `docker/entrypoint.sh` expects `DB_HOST` and `DB_PORT` to be available for every container role

### `apps.authentication`

Current responsibilities:

- custom user model
- login / registration
- JWT token creation
- active profile switching
- JWT profile-claim synchronization through `ProfileSyncJWTAuthentication`

Current auth API:

- `/api/auth/register/customer/`
  - `POST`: public customer registration
- `/api/auth/register/manager/`
  - `POST`: currently public manager registration; this is a known security gap
- `/api/auth/login/`
  - `POST`: token pair login using the custom token serializer
- `/api/auth/token/refresh/`
  - `POST`: SimpleJWT refresh endpoint
- `/api/auth/switch-profile/`
  - `POST`: authenticated active profile switch by `new_profile` type value

Current auth behavior:

- `ppw/settings/base.py` uses `apps.authentication.authentication.ProfileSyncJWTAuthentication`
- JWTs must include a `profile` claim
- token `profile` values use the exact `ProfileType` stored values:
  - `Seller`
  - `Customer`
  - `Manager`
- `build_token_for_user_role()` writes:
  - `profile`
  - `user_id`
  - `is_activated`
- `build_token_for_user()` reads the first `Profile` row with `is_active=True`; it does not call the stricter `user.active_profile` property
- a token without `profile` is rejected with authentication failure
- if token `profile` differs from `request.user.role`, authentication attempts `switch_user_profile(user, new_profile_type=token_profile)`
- if profile switching fails, authentication continues with the current active profile
- `switch_user_profile()` returns a `(success, profile_type)` style result
- `switch_user_profile()` switches by profile type, not profile id
- `switch_user_profile()` currently refuses to switch away from a current `Manager` role
- `switch_user_profile()` does not currently filter the target profile by `activated`, `is_valid`, or `deleted__isnull=True` before setting `is_active=True`

Must not own:

- asset or auction business rules
- profile business data outside auth/session concerns

### `apps.users`

Current responsibilities:

- profile types
- seller / customer / manager profiles
- current active profile
- profile validity and activation state
- profile-level business identity

Current user/profile API:

- `/api/users/admin/sellers/`
  - admin/staff CRUD for seller profiles through `SellerProfileAdminViewSet`
- `/api/users/admin/customers/`
  - admin/staff CRUD for customer profiles through `CustomerProfileAdminViewSet`
- `/api/users/mobile/sellers/`
  - authenticated user-facing seller profile API
- `/api/users/mobile/customers/`
  - authenticated user-facing customer profile API

Current profile behavior:

- `active_profile` requires `is_active=True`, `is_valid=True`, and `activated=True`
- `Profile.is_valid` is the current punishment / disabled-profile flag
- exactly one `is_active=True` profile per user is enforced through the active-profile uniqueness constraint
- persisted profile type values are title-case strings: `Seller`, `Customer`, and `Manager`
- `Profile.save()` derives `type` from the concrete subclass
- customer registration creates an active customer profile
- manager registration creates an active, activated manager profile and sets `is_staff=True`
- mobile seller profile creation creates an inactive seller profile and overwrites submitted `nickname` with `user.username`
- deleting active mobile customer or seller profiles marks them inactive/invalid, soft-deletes them, and attempts to switch to the first other profile

Must not own:

- JWT generation
- asset or auction workflows

### `apps.assets`

Current responsibilities:

- asset domain
- shared asset commerce fields
- birds as first-class domain objects
- minimal non-bird product subclasses
- product asset CRUD for supply, accessory, feed, and supplement subtypes
- product asset filtering, search, and ordering via django-filter
- bird validation

Future home:

- bird-related domain extensions
- media/file reference entities that store file keys uploaded by the frontend to S3 or similar

Important note:

This app currently models domain assets, not a full media pipeline.

Current model shape:

- `Asset` remains the concrete shared base model
- asset classification now uses `category`, not `asset_type`
- `Asset.count` is still a shared integer field on `Asset`; product-specific stock modeling is not split out yet
- current asset categories are:
  - `bird`
  - `supply`
  - `accessory`
  - `feed`
  - `supplement`
- shared commerce fields now live on `Asset`, including:
  - `price`
  - `is_market_listed`
  - `is_for_auction`
- current asset statuses are:
  - `available`
  - `sold`
  - `inactive`
- `Bird`, `Supply`, `Accessory`, `Feed`, and `Supplement` are subclasses of `Asset`

Current bird API:

- `/api/assets/birds/`

Current bird API behavior:

- `BirdViewSet` is a DRF `ModelViewSet`
- `BirdSerializer` sets `owner` from `request.user.active_profile` on create
- `owner` is read-only in API input
- `BirdValidator` enforces seller ownership, future birthday rejection, non-negative flying speed, parent gender, parent age, and no self-parent
- `Bird.save()` forces `count=1`
- `Bird.save()` also inherits subtype category enforcement, so `Bird.category` is always `bird`

Current product asset API:

- `/api/assets/supplies/`
- `/api/assets/accessories/`
- `/api/assets/feeds/`
- `/api/assets/supplements/`

Current product asset API behavior:

- product CRUD uses shared `ProductAssetSerializer`
- `owner` and `category` are read-only in API input
- create sets `owner` from `request.user.active_profile`
- subtype `save()` remains responsible for enforcing `category`
- product serializer field constants live in `apps/assets/constants.py`
- product filters live in `apps/assets/filters.py`
- product routes use `DjangoFilterBackend` and DRF `OrderingFilter`

Current product asset query support:

- exact filters:
  - `status`
  - `owner`
  - `is_market_listed`
  - `is_for_auction`
- range filters:
  - `min_price`
  - `max_price`
  - `min_count`
  - `max_count`
- text filters:
  - `search`
  - `q`
- ordering fields:
  - `price`
  - `count`
  - `created`
  - `modified`
  - `title`
  - `status`

### `apps.orders`

Current responsibilities:

- persistent customer cart models and cart item models
- order aggregate models
- order item models
- per-item seller linkage for multi-seller orders
- order and order-item status persistence
- price snapshot storage for future settlement flows
- cart, checkout, and compatibility direct-order API / serializer / validator / service flow
- seller approval and rejection of individual order items
- seller-approval-time stock decrement for fixed-price market order items
- seller-decision timeout auto-cancellation for stale pending order items
- lightweight order event records for future notification hooks

Important note:

This app currently provides the first cart and fixed-price checkout workflow layer. It does not yet implement payment review, handoff confirmation, settlement workflow, or ownership transfer workflow.

Current order API:

- `/api/orders/cart/`
  - `GET`: customer retrieves or lazily creates their active cart
- `/api/orders/cart/items/`
  - `POST`: customer adds an asset to the active cart; duplicate assets merge quantities
- `/api/orders/cart/items/<id>/`
  - `PATCH`: customer updates one active cart item quantity
  - `DELETE`: customer removes one active cart item
- `/api/orders/cart/clear/`
  - `DELETE`: customer clears all active cart items
- `/api/orders/checkout/`
  - `POST`: customer converts the full active cart into an order after validating stock availability
- `/api/orders/`
  - `GET`: paginated customer list of own orders or manager list of all orders; supports exact `status` filtering
  - `POST`: compatibility alias for direct order creation from market-listed seller assets; validates stock availability without decrementing stock
- `/api/orders/<id>/`
  - `GET`: customer retrieves own order; manager retrieves any order
- `/api/orders/items/`
  - `GET`: paginated seller list of own order items or manager list of all order items
- `/api/orders/items/<id>/approve/`
  - `POST`: seller approves their own pending order item
- `/api/orders/items/<id>/reject/`
  - `POST`: seller rejects their own pending order item

Current order behavior:

- each customer can have one active cart; active carts are created lazily
- order lists can be filtered by exact aggregate order `status`
- customers can add only available, market-listed assets with enough stock from valid seller profiles
- adding the same asset to a cart increases the existing cart item quantity instead of creating a duplicate row
- cart add and update store a `unit_price_snapshot`, but checkout revalidates using current DB asset prices
- duplicate `asset_id` entries in one order payload are rejected
- customers cannot order or cart assets owned by another profile on the same user account
- checkout runs in a DB transaction and locks the active cart plus all checked-out asset rows
- checkout revalidates cart items under lock and uses latest `Asset.price`, not stale cart price snapshots
- checkout converts the active cart to `converted`; a later `GET /api/orders/cart/` lazily creates a new active cart
- order creation stores `total_price` on `Order`, plus `unit_price_snapshot` and `total_price` on each item
- checkout and direct `POST /api/orders/` validate stock but do not decrement `Asset.count`
- checkout and direct `POST /api/orders/` do not mark assets `sold` and do not unlist assets from the market
- new order items start as `pending_seller`
- seller approval is the stock mutation point and revalidates the locked asset before decrementing `Asset.count`
- seller approval revalidation checks that the asset is not soft-deleted, has status `available`, and has enough count
- seller approval does not require the asset to still be market-listed
- seller approval changes `pending_seller` to `approved` after successful stock decrement
- when seller approval decrements stock to zero, the asset is marked `sold` and unlisted from the market
- seller approval is idempotent when the item is already `approved`
- seller rejection changes `pending_seller` to `rejected` only; it does not change stock, asset status, or market listing
- seller rejection is idempotent when the item is already `rejected`
- pending seller order items older than `ORDER_ITEM_SELLER_DECISION_TIMEOUT_DAYS` are automatically moved to `cancelled` by a Celery task
- auto-cancelling a pending order item refreshes the parent order status but does not change stock, asset status, or market listing
- aggregate order statuses are `pending`, `processing`, `completed`, `cancelled`, and `partial_rejected`
- if all order items are rejected or cancelled, the aggregate order status becomes `cancelled`
- if all order items are completed, the aggregate order status becomes `completed`
- approved, awaiting-payment-review, ready-handoff, or completed items can move the aggregate order status to `processing`
- any rejected item plus any non-rejected item makes the aggregate order status `partial_rejected`
- lightweight `OrderEvent` records are created for order creation, seller pending item notifications, seller approval, and seller timeout auto-cancellation
- seller rejection currently does not create an `OrderEvent`
- ownership transfer is not implemented yet

Current order service implementation names:

- `OrderCheckoutService._create_pending_order()` creates the order and pending seller items for cart checkout and direct order creation
- `OrderCheckoutService._ensure_stock_available()` validates count during checkout/order creation without mutating assets; purchase validators enforce listed/status/seller/customer rules before this helper runs
- `OrderItemService._apply_stock_deduction()` decrements stock on seller approval and marks zero-count assets sold/unlisted
- `OrderItemService.cancel_expired_pending_items()` cancels stale `pending_seller` order items whose `created` timestamp is outside the configured seller decision window

### `apps.auctions`

Current responsibilities:

- auctions
- auction items
- bids
- auction events
- auction business validation
- bidding orchestration
- auction closing and winner resolution logic
- limited auction editing through service-layer update locking

This app currently has the clearest service / validator / factory layering in the repo and should be the reference pattern for future backend workflows.

Important note:

Auction closing currently resolves winners and marks items sold or unsold. It does not implement payment review, handoff confirmation, ownership transfer, dispute handling, or post-auction market completion.

Current auction API:

- `/api/auctions/`
  - `GET`: authenticated list of visible auctions for allowed roles
  - `POST`: seller creates an auction with nested items
- `/api/auctions/<id>/`
  - `GET`: retrieve auction detail, subject to current object permission behavior
  - `PATCH`: owner or manager updates only `title`, `description`, and `tags` while editable
  - `DELETE`: owner or manager soft-deletes through the default `ModelViewSet` destroy path
- `/api/auctions/my_auctions/`
  - `GET`: seller lists own auctions, optionally filtered by `status`
- `/api/auctions/active/`
  - `GET`: list visible auctions whose time window is currently active
- `/api/auctions/ending_soon/`
  - `GET`: list visible auctions ending within the requested `minutes`
- `/api/auctions/<id>/cancel/`
  - `POST`: owner or manager cancels an auction through `AuctionService.cancel_auction`
- `/api/auctions/<id>/close/`
  - `POST`: admin/staff manually closes an auction and resolves item winners
- `/api/auctions/items/`
  - `GET`: read-only auction item list, optionally filtered by `auction_id`
- `/api/auctions/items/<id>/place_bid/`
  - `POST`: authenticated bid placement
- `/api/auctions/items/<id>/buy_now/`
  - `POST`: authenticated buy-now action
- `/api/auctions/bids/`
  - `GET`: read-only bid list, optionally filtered by `item_id`
- `/api/auctions/bids/my_bids/`
  - `GET`: authenticated current-profile bid list
- `/api/auctions/events/`
  - `GET`: read-only auction event list, optionally filtered by `auction_id`

Current auction behavior:

- auction type values are `single`, `multi`, `pair`, `breeding`, and `racing`
- auction statuses are `active`, `ended`, `cancelled`, and `closed`
- auction item statuses are `active`, `sold`, `unsold`, `withdrawn`, and `cancelled`
- auction creation requires future `start_time`, later `end_time`, and nested item payloads
- `AuctionBusinessValidator` enforces item counts by auction type, pair gender rules, duplicate bird rejection, overlapping active-auction bird rejection, auto-extend config, and deposit config
- `AuctionBusinessValidator` currently does not enforce that every submitted bird belongs to the requesting seller
- `AuctionService.create_auction()` writes a `created` event
- `BidBusinessValidator` requires a customer profile, an active time window, an active item, starting-price compliance, and minimum increment compliance
- bid placement revalidates under an `AuctionItem` row lock before creating the bid
- bid placement updates `AuctionItem.current_price`, optionally auto-extends, and writes a `bid_placed` event
- auto-extend uses the module constant `AUCTION_AUTO_EXTEND_MINUTES = 10` as the trigger window and extends by `auction.auto_extend_minutes`
- `AuctionService.update_auction()` allows updates only when status is `active`, the start time is still in the future, and no bids exist
- scheduled closing is handled by Celery Beat through `auctions.tasks.close_expired_auctions`
- `AuctionService.close_expired_auctions()` selects active auctions with `end_time <= now`
- `AuctionService.close_auction()` is idempotent for already closed auctions
- closing an auction marks each item `sold` with winner data when bids exist, otherwise `unsold`, sets the auction to `closed`, and writes an `auction_ended` event
- buy-now creates a bid at `auction.buy_now_price`, marks the item sold, sets winner fields, updates current price, and writes a `buy_now_triggered` event

### `apps.ratings`

Current responsibilities:

- asset comments and seller comments
- customer ratings for assets and sellers through separate concrete models
- rating eligibility validation against completed order items
- cached rating aggregate refresh for assets and seller profiles

Current ratings/comment API:

- `/api/asset-comments/`
  - `GET`: authenticated paginated asset comment list; supports `asset_id`, `owner_id`, and ordering filters
  - `POST`: authenticated active-profile asset comment creation with nested `asset`
- `/api/seller-comments/`
  - `GET`: authenticated paginated seller comment list; supports `seller_id`, `owner_id`, and ordering filters
  - `POST`: authenticated active-profile seller comment creation with nested `seller`
- `/api/asset-ratings/`
  - `GET`: authenticated paginated asset rating list; supports `asset_id`, `stars`, and ordering filters
  - `POST`: authenticated customer-profile asset rating creation with nested `asset` and optional nested `comment`
- `/api/seller-ratings/`
  - `GET`: authenticated paginated seller rating list; supports `seller_id`, `stars`, and ordering filters
  - `POST`: authenticated customer-profile seller rating creation with nested `seller` and optional nested `comment`

Current comment behavior:

- `Comment` is an abstract base class for shared comment fields
- concrete comment models are `AssetComment` and `SellerComment`
- comments inherit `OwnerBaseModel`; owner is the active `Profile`
- comments target exactly one asset or one seller through their concrete model
- any authenticated user with an active valid profile can comment on any asset or seller
- no completed order item is required for commenting
- the same owner cannot create more than one comment on the same target within 2 minutes
- standalone comment endpoints always create `is_rating_comment=False`
- rating-related comments can only be created through nested rating API payloads and use `is_rating_comment=True`
- comments do not support replies, moderation, reporting, likes, notifications, or analytics

Current rating behavior:

- `Rating` is an abstract base class for shared rating fields
- concrete rating models are `AssetRating` and `SellerRating`
- ratings store `owner` as `CustomerProfile`
- ratings target exactly one asset or one seller through their concrete model
- ratings can optionally create a nested rating comment during rating creation
- rating APIs do not link existing comments and reject nested `comment.id`
- ratings require `stars` from 1 through 5 through model field validators and API serializer validation
- customers can rate an asset only after a completed `OrderItem` for that asset
- customers can rate a seller only after a completed `OrderItem` for that seller
- seller and manager active profiles cannot create ratings
- duplicate active ratings are blocked per customer/asset and per customer/seller
- rating creation recomputes `Asset.avg_rating`, `Asset.ratings_count`, `SellerProfile.avg_rating`, and `SellerProfile.ratings_count`
- rating update/delete, moderation, replies, reports, likes, notifications, points, and analytics are not implemented

### `apps.races`

Current responsibilities:

- race/report records for pigeon or bird racing
- race result records with denormalized competitor and bird ring details
- automatic race standing rank recalculation
- global race-result search

Current race API:

- `/api/races/`
  - `GET`: authenticated paginated race list
  - `POST`: manager-only race creation
- `/api/races/<id>/`
  - `GET`: authenticated race detail with nested results
  - `PUT/PATCH/DELETE`: manager-only race updates and deletion
- `/api/races/<id>/standings/`
  - `GET`: authenticated paginated results for one race ordered by rank
- `/api/races/<id>/results/bulk/`
  - `POST`: manager-only bulk result submission for one race
- `/api/race-results/`
  - `GET`: authenticated paginated global result search
  - `POST`: manager-only individual result creation
- `/api/race-results/<id>/`
  - `GET`: authenticated result detail
  - `PUT/PATCH/DELETE`: manager-only result updates and deletion

Current race behavior:

- only two race-domain models exist: `Race` and `RaceResult`
- there are no separate competitor, club, season, or race-bird models
- `RaceResult.competitor_name` and `RaceResult.bird_ring_number` are plain fields
- `RaceResult.rank` is calculated by service code and is not trusted from request payloads
- rank recalculation sorts by highest `speed`, then earliest `arrival_datetime`, then lowest `id`
- `recalculate_race_ranks(race_id)` is the service-layer ranking entry point
- creating, updating, deleting, or bulk-submitting results recalculates all ranks for the race inside transactions
- active `RaceResult` rows enforce unique `bird_ring_number` per race and unique `rank` per race
- `speed` and `distance_km` must be greater than zero
- `arrival_datetime` must be after the parent race `release_datetime`
- global result search supports `bird_ring_number`, `competitor_name`, `q`, and `race_id`
- `include_race=true` on race-result list/detail responses includes a nested race summary; false or omitted excludes it
- race APIs use `HasAppAccess` with `allow_scoped_read_access=True`
- managers can write races and results; authenticated sellers and customers can read races, standings, and race-result search

### `apps.general`

Current responsibilities:

- base models
- permissions / authorization helpers
- shared constants / helpers

### Shared Abstractions

- `apps.general.models.BaseModel`: timestamped + soft-delete base model
- `apps.general.models.OwnerBaseModel`: common owner relation to `users.Profile`
- `apps.general.models.ResourcePermission`: persisted resource/owner placeholder; current `HasAppAccess` does not consult this model
- `apps.general.authorizations.HasAppAccess`: repo-specific permission gate used by app viewsets
- `HasAppAccess.allow_scoped_read_access`: opt-in read behavior for resources whose `get_queryset()` already scopes readable objects
- `HasAppAccess.permission_owner_field`: optional view attribute for object checks that use a non-`owner_id` ownership field, such as `OrderItem.seller_id`
- `apps.general.viewsets.CreateListViewSet`: shared DRF base for endpoints that only need `create` and `list`

### Main Domain Concepts

#### UserAccount

Authentication identity. A user may have multiple profiles. The active profile determines current behavior and permission context.

#### Profile

Profile is the real business actor in the system.

Current profile types:

- `Seller`
- `Customer`
- `Manager`

Important implementation detail:

These are exact persisted and JWT claim values from `ProfileType`, not lowercase slugs.

Important rule:

Use `active_profile` as the operational actor in business flows.

DRY active-profile rule:

- `user.active_profile` is the source of truth for the current valid active profile
- do not duplicate `active_profile` validity checks for `is_active`, `is_valid`, `activated`, or `deleted` after retrieving `user.active_profile`
- keep role/type checks such as `Customer`, `Seller`, or `Manager` when they are business rules for the action
- keep validation for other profiles or objects, such as asset owners, when they are not the current user's `active_profile`
- `user.active_profile` returns the base `Profile`; fetch the concrete subtype only when assigning to a concrete FK such as `CustomerProfile` on ratings or orders

#### Bird

Bird is a core domain entity. Current owner is the current profile owner of the bird. Bird may have parent references. Bird can be listed in auctions and later in market flows.

Current design choice:

- current owner is stored now
- full ownership history is intentionally not implemented yet
- near-term planned simplification is to store previous owner only
- `Bird` remains a subclass of `Asset`
- `Bird.category` is always `bird`
- bird listing flags are shared through `Asset`, not duplicated on `Bird`

#### Asset

Asset is the shared concrete base model for commerce-capable domain items.

Current design choice:

- subtype classification is stored in `category`
- `Asset` carries shared listing and pricing fields
- non-bird product types currently exist as minimal subclasses for future extension
- cart and checkout services enforce fixed-price market purchase rules on top of these fields

#### Cart

Cart is the persistent server-side customer cart for fixed-price market flows.

Current design choice:

- `Cart.customer` is a `CustomerProfile`
- one active cart per customer is enforced at the database level
- cart statuses are `active`, `converted`, and `abandoned`
- cart retrieval creates an active cart lazily when none exists

#### CartItem

CartItem is one asset row inside a customer cart.

Current design choice:

- each cart item references an `Asset` and a `SellerProfile`
- one cart row per asset per cart is enforced at the database level
- duplicate add-to-cart requests increase quantity on the existing row
- `unit_price_snapshot` records the price at cart-add time, but checkout uses latest `Asset.price`

#### Order

Order is the top-level purchase container for market flows.

Current design choice:

- `Order` inherits `OwnerBaseModel`
- `Order.owner` is a `CustomerProfile`, not a generic `Profile`
- an order may contain items from multiple sellers
- `Order.status` is an aggregate summary field, not the primary business decision unit
- `Order.total_price` is a stored checkout-time decimal snapshot
- there is no seller field on `Order`

#### OrderItem

OrderItem is the real market business unit.

Each item has:

- direct `Asset` reference
- explicit `SellerProfile` reference
- quantity
- unit price snapshot
- total price
- independent status

Current order-item statuses:

- `pending_seller`
- `approved`
- `rejected`
- `awaiting_payment_review`
- `ready_handoff`
- `completed`
- `cancelled`

Important rule:

Future settlement and approval logic should operate at the order-item level, not only at the order level.

#### OrderEvent

OrderEvent is a lightweight record for order-related notification and traceability placeholders.

Current design choice:

- events are written synchronously by order services
- current event types include order creation, seller pending item, seller approval, and seller timeout auto-cancellation
- there is no push delivery, async notification task, or external notification integration yet

#### Comment

Comment is an abstract base for open authenticated discussion records.

Current design choice:

- concrete comment models are `AssetComment` and `SellerComment`
- comment owner is the active `Profile`
- any authenticated active profile can create standalone asset or seller comments
- `is_rating_comment=True` is reserved for comments created through nested rating API payloads
- standalone comment create payloads use nested targets, for example `{"asset": {"id": 1}, "text": "..."}` or `{"seller": {"id": 2}, "text": "..."}`
- comment throttling is validation-layer only, not a database constraint
- comments are flat; replies/threading and moderation/reporting are not implemented

#### Rating

Rating is an abstract base for controlled customer feedback.

Current design choice:

- concrete rating models are `AssetRating` and `SellerRating`
- rating owner is a `CustomerProfile`
- rating comments are created only through nested rating API payloads
- rating create payloads use nested targets, for example `{"asset": {"id": 1}, "stars": 5, "comment": {"text": "..."}}` or `{"seller": {"id": 2}, "stars": 5}`
- rating APIs reject flat `target_type` / `target_id` payloads and reject nested `comment.id`
- concrete rating models use field validators for one-to-five `stars`; API serializers also validate the range
- completed `OrderItem` eligibility is required before rating creation
- rating aggregates are cached on `Asset` and `SellerProfile`

#### Race

Race is one pigeon or bird racing report.

Current design choice:

- `Race` stores report-level details such as season year, station, release datetime, weather, total birds, competitor count, optional planned distance, coordinates, and notes
- no separate `Season` or `Club` model exists for race reports
- race records are manager-written and authenticated-user readable
- race details can include nested results

#### RaceResult

RaceResult is one reported bird result inside a race.

Current design choice:

- each result belongs to one `Race`
- competitor name and bird ring number are stored directly on the result
- no separate race `Bird` or `Competitor` model exists
- rank is recalculated by service logic from speed within the race
- higher speed ranks better
- ties are deterministically ordered by arrival datetime and id because rank remains unique per race
- result writes must use the service layer so the race standings stay consistent

#### Auction

Auction is the top-level aggregate for timed bidding. It contains one or more auction items. Auction type determines creation and validation rules.

Current design choice:

- `Auction.status` is lifecycle state, but `Auction.is_active()` currently checks only the time window
- scheduled expiry moves active auctions to `closed`, not `ended`
- `ended` remains a model choice but is not the normal scheduled close outcome

#### AuctionItem

AuctionItem is the unit of bidding and sale.

Each item has:

- primary bird
- optional paired bird
- independent bid stream
- status
- winner data

Current design choice:

- `AuctionItem.status`, `winner`, and `winner_bid` are resolved when an auction is closed
- sold auction items do not yet trigger payment, handoff, or ownership-transfer workflow

#### Bid

Represents a bid placed by a customer profile on an auction item.

#### AuctionEvent

Audit or event stream for auction lifecycle actions. It is used for traceability and future notification hooks. It is not a full audit system yet.

### Request / Data Flow

The expected backend flow in this repo is:

`urls` -> DRF `APIView` / `ViewSet` -> serializer input validation / shape validation -> validator / service / factory layer -> models -> response serializer

For filtered list endpoints, apply visibility scoping before user-selected filters:

`ViewSet.get_queryset()` -> `DjangoFilterBackend` / ordering -> response serializer

For auction workflows, `apps/auctions/services.py` already documents the clearest current 4-layer flow:

1. Input validation in serializers
2. Business validation in validator classes
3. Object creation in factories
4. Transactional orchestration in services

### Global Auth / Permission Defaults

From `ppw/settings/base.py`:

- authentication defaults to JWT via `apps.authentication.authentication.ProfileSyncJWTAuthentication`
- default schema generation uses `drf_spectacular.openapi.AutoSchema`
- default DRF permission is `IsAuthenticated`
- global DRF list pagination uses `PageNumberPagination` with `PAGE_SIZE = 20`
- paginated list responses use DRF's `count`, `next`, `previous`, and `results` response shape
- `AUTH_USER_MODEL` is `authentication.UserAccount`
- `APPEND_SLASH = False`

Important JWT rule:

- access tokens must contain a `profile` claim
- test helpers should use `build_token_for_user()` unless a test intentionally needs a raw token without the profile claim

Mounted API roots:

- `/api/auth/`
- `/api/users/`
- `/api/assets/`
- `/api/auctions/`
- `/api/orders/`
- `/api/asset-comments/`
- `/api/seller-comments/`
- `/api/asset-ratings/`
- `/api/seller-ratings/`
- `/api/races/`
- `/api/race-results/`

Current internal API docs endpoints:

- `/api/schema/`
  - public when `DEBUG=True`
  - requires any authenticated user when `DEBUG=False`
- `/api/docs/swagger/`
  - mounted only when `DEBUG=True`

Current schema/auth documentation behavior:

- drf-spectacular uses an explicit bearer auth schema for `ProfileSyncJWTAuthentication`
- protected endpoints show Bearer/JWT auth in the generated OpenAPI schema
- login remains unauthenticated in schema output
- request and response components are split for clearer required/read-only field documentation

## Core Business Rules

### Identity And Profiles

- a user authenticates as `UserAccount`
- business actions are performed through `active_profile`
- manager is an administrative and business profile, not just a Django staff concept
- seller and customer capabilities should remain profile-based
- token `profile` and database `active_profile` are synchronized during JWT authentication when possible
- missing token `profile` is an authentication error
- failed token-driven profile switching is currently a no-op, not a request failure

### Auctions

- auctions are created by sellers
- bidding is done by customers
- sellers must not bid on auctions
- current partial update permits only `title`, `description`, and `tags`
- current partial update is blocked after auction start, after any bid, or when status is not `active`
- birds should not be placed in overlapping active auctions
- auction state transitions must be explicit and safe
- scheduled auction expiry transitions auctions to `closed` and resolves item winner fields
- full post-auction completion remains planned, not implemented

### Payments

There is no gateway payment integration.

Planned approach:

- winner submits manual payment proof or payment intent evidence
- admin confirms or rejects payment manually
- system tracks payment review state
- no automated settlement, no capture, no refund engine

### Handoff / Delivery

There is no shipping integration.

Planned approach:

- seller confirms handoff or delivery manually
- optional buyer acknowledgment may be added later
- ownership transfer happens after payment approval and handoff confirmation

### Ownership

Current short-term approach:

- only current owner is stored now
- full ownership history is intentionally deferred
- ownership transfer must happen only via service layer
- do not mutate ownership casually from serializers or views

### Assets / Media

Current assets app is domain modeling, not file/media handling.

Current e-commerce foundation:

- assets can now carry shared price and listing fields
- product assets now have CRUD endpoints for supplies, accessories, feeds, and supplements
- product asset list endpoints support filtering, search, and ordering
- carts, checkout, orders, and order items now have an API / serializer / validator / service layer
- order aggregate list endpoints support exact `status` filtering
- customers can add listed seller assets to a persistent server-side cart
- customers can checkout the full active cart into a pending seller decision market order
- direct `POST /api/orders/` remains available as a compatibility alias with the same stock-validation semantics
- sellers can approve or reject their own order items
- checkout-time stock validation does not decrement stock or change listing state
- seller approval updates stock and can mark sold-out assets unavailable
- seller rejection updates item/order statuses but does not change stock
- lightweight order events exist as notification placeholders
- this does not mean market workflows are complete yet
- payment review, handoff, settlement, and ownership transfer are still not implemented

Current product asset visibility:

- managers can list, retrieve, create, update, and delete all product assets
- manager-created products are owned by the manager active profile because create always sets `owner=request.user.active_profile`
- sellers can create, update, and delete only their own product assets
- sellers can read their own product assets plus market-listed product assets
- customers can read only market-listed product assets
- product safe-read visibility is scoped before django-filter filters and ordering run

Planned direction:

- bird images
- bird video
- pedigree attachments
- race result attachments
- optional payment proof attachments

Keep initial implementation simple:

- basic file models or references
- validation
- relations to domain objects
- no heavy media processing unless required
- frontend uploads files to S3 or similar and backend stores the file key

## Architectural Principles

### Modular Monolith

Do not split into microservices. Keep domain logic separated by app boundaries inside the monolith.

### Explicit Business Services

Critical workflows should go through service methods.

Examples:

- place bid
- create auction
- checkout cart
- create pending seller decision market order
- cancel auction
- approve payment
- confirm handoff
- transfer ownership

### Validators For Business Rules

Serializer validates request shape. Business validator validates domain rules. Do not bury core business rules inside serializers.

### Factories Only Where Useful

Factories are acceptable for constructing domain objects cleanly. Do not create factories for trivial CRUD.

### Keep Views Thin

Views and viewsets should orchestrate request and response only.

### Transaction Safety

Use `transaction.atomic()` for critical write flows:

- bid placement
- cart checkout and direct market order creation
- seller approval stock decrement
- settlement
- payment approval
- ownership transfer

### Idempotent Critical Actions

Actions like these must be protected from duplicate execution:

- approve payment
- reject payment
- confirm handoff
- transfer ownership

### No Hidden Side Effects

Avoid signals for core business logic. Prefer explicit service calls.

### Simplicity First

Implement the smallest correct solution first. Do not over-engineer for hypothetical future use cases.

## Coding And Contribution Conventions

### General

- match current repo style
- preserve existing naming unless there is a strong correction needed
- prefer short, framework-native action names such as `retrieve`, `post`, `patch`, and `delete` where practical
- explain endpoint-specific behavior through schema summaries/descriptions and docs instead of long customized method names
- avoid large refactors during feature work
- do not change unrelated files

### Layering

Preferred layering:

- models: structure and lightweight invariants
- serializers: request/data validation and representation
- validators: business validation
- services: orchestration and state changes
- views: HTTP layer only

Shared DRF viewset rule:

- when implementing an endpoint that only supports `create` and `list`, prefer `apps.general.viewsets.CreateListViewSet`
- define `create_serializer_class` for write/input validation and `list_serializer_class` for read/list output
- create responses from `CreateListViewSet` intentionally use `list_serializer_class`, not the create serializer
- do not duplicate this base viewset inside individual apps
- keep endpoint-specific queryset scoping, `permission_resource`, filters, and extra ordering fields on the concrete viewset

Nested serializer rule:

- prefer `WritableNestedModelSerializer` for nested create/update serializers
- use nested request objects for naturally nested APIs instead of flat `target_type` / `target_id` payloads
- return nested summaries in responses for APIs that accept nested request objects
- keep plain `Serializer` only for non-nested command payloads

### Permissions

Permissions must be checked at:

- endpoint level
- object level where relevant

Do not assume authenticated user is enough. Profile type and ownership matter.

Current permission resources:

- `BIRD`
- `PRODUCT_ASSET`
- `AUCTION`
- `CART`
- `CHECKOUT`
- `ORDER`
- `ORDER_ITEM`
- `ASSET_COMMENT`
- `SELLER_COMMENT`
- `ASSET_RATING`
- `SELLER_RATING`
- `RACE`
- `RACE_RESULT`

Permission implementation rule:

- do not introduce any new permission class without explicit user approval
- new endpoint authorization must use `apps.general.authorizations.HasAppAccess` plus `CUSTOMER_PERMISSIONS` / `SELLER_PERMISSIONS` unless the user explicitly approves another approach
- viewsets using `HasAppAccess` must define both `queryset` and `permission_resource`
- keep profile-specific business validation in serializers, validators, or services when `HasAppAccess` is only the endpoint gate

Current product asset permission pattern:

- use `HasAppAccess`
- set `permission_resource = "PRODUCT_ASSET"`
- set `allow_scoped_read_access = True`
- scope readable objects in `get_queryset()` before filters run

Current bird permission pattern:

- `BirdViewSet` uses `HasAppAccess` with `permission_resource = "BIRD"`
- `BirdViewSet` does not currently scope `get_queryset()` by role, owner, or listing state
- list access is therefore broader than object retrieve/update/delete checks
- create still depends on serializer validation that requires an active seller profile as owner

Current order permission pattern:

- `OrderViewSet` uses `HasAppAccess` with `permission_resource = "ORDER"`
- customers can list, retrieve, and create only their own orders through `Order.owner`
- cart endpoints use `ActiveCartViewSet` plus `HasAppAccess` with `permission_resource = "CART"`
- current `CART` customer actions are `retrieve`, `post`, `patch`, `remove_cart_item`, and `delete`
- checkout uses `CheckoutViewSet` plus `HasAppAccess` with `permission_resource = "CHECKOUT"`
- current `CHECKOUT` customer action is `post`
- cart schema tags are `Cart`; checkout schema tags are `Checkout`
- managers currently pass `HasAppAccess` for cart and checkout because manager bypass remains global; service validation still requires a customer profile for cart/checkout execution
- dynamic per-user resource permissions are planned after most resources are implemented
- authenticated users with no active valid role are denied by `HasAppAccess` before resource action checks run
- sellers must switch to customer mode before using cart or checkout endpoints
- sellers do not access `/api/orders/`; seller order work happens through order-item endpoints
- `OrderItemViewSet` uses `HasAppAccess` with `permission_resource = "ORDER_ITEM"`
- order-item object checks use `permission_owner_field = "seller_id"`
- sellers can list, approve, and reject only their own seller-side order items
- managers can access all order and order-item actions through existing manager behavior

Current ratings/comment permission pattern:

- `AssetCommentViewSet` uses `HasAppAccess` with `permission_resource = "ASSET_COMMENT"`
- `SellerCommentViewSet` uses `HasAppAccess` with `permission_resource = "SELLER_COMMENT"`
- `AssetRatingViewSet` uses `HasAppAccess` with `permission_resource = "ASSET_RATING"`
- `SellerRatingViewSet` uses `HasAppAccess` with `permission_resource = "SELLER_RATING"`
- customers and sellers can list and create comments through shared permission constants
- customers can list and create asset/seller ratings through shared permission constants
- sellers can list asset/seller ratings but cannot create ratings
- rating creation remains customer-only through service validation after the shared endpoint permission gate
- ratings and comments must not use app-local permission classes

Current race permission pattern:

- `RaceViewSet` uses `HasAppAccess` with `permission_resource = "RACE"`
- `RaceResultViewSet` uses `HasAppAccess` with `permission_resource = "RACE_RESULT"`
- both race viewsets set `allow_scoped_read_access = True` because readable race data is intentionally available to any authenticated role
- managers can create, update, delete, and bulk-submit race data through the global manager bypass
- sellers and customers can list/retrieve races, list standings, and search race results only
- race write validation and rank recalculation live in serializers/services, not in permission classes

Current auction permission pattern:

- `AuctionViewSet` uses `IsAuthenticated` plus `HasAppAccess` with `permission_resource = "AUCTION"`
- sellers can create and manage their own auctions through owner-scoped querysets for owner actions
- managers bypass `AuthorizationService` checks when their active role is `Manager`
- customers can use configured auction read actions, but `AuctionViewSet` does not set `allow_scoped_read_access`
- `AuctionItemViewSet`, `BidViewSet`, and `AuctionEventViewSet` use DRF read-only public permissions for list/retrieve and custom auth checks on write actions
- `place_bid` requires authentication and is restricted to customer profiles by `BidBusinessValidator`
- `buy_now` requires authentication but currently does not enforce `Customer` profile type in the same way as regular bidding

Important scoped-read rule:

- `allow_scoped_read_access=True` only bypasses owner-only object checks for `SAFE_METHODS`
- use it only when `get_queryset()` correctly limits readable objects for the current user
- do not set it on write-heavy or owner-only resources unless the read scope is explicitly reviewed

### Query Discipline

Avoid N+1 patterns on list/detail endpoints. Use `select_related` and `prefetch_related` when necessary.

Use `django-filter` for structured list filtering when adding new filtered DRF endpoints. Prefer `FilterSet` classes over custom filter parsing unless the query behavior cannot be expressed cleanly with django-filter.

### Auditability

At minimum, important actions should produce traceable events or fields. A more complete audit trail may be needed later.

### Repo Rules

- read this file first before making repo changes
- treat current behavior as source-of-truth unless the task asks for a refactor or policy change
- do not impose a new architecture unless the current approach clearly fails for the requested task
- ask before changing business rules, permission semantics, or public API contracts
- keep changes simple, correct, and maintainable first
- prefer extending an existing service / validator / factory pattern over inventing a new one
- move constants longer than 15 lines into the app's `constants.py` instead of keeping them inline
- add or update focused tests in the touched area
- expand to broader regression coverage only when a change crosses app boundaries
- avoid signals and hidden side effects

## Current State

The codebase currently provides:

- auth, profile switching, and JWT profile-claim synchronization
- internal OpenAPI schema generation and Swagger UI for development teams
- asset base model with shared market/listing fields
- bird entity, CRUD endpoint, and validation
- product asset CRUD for supplies, accessories, feeds, and supplements
- product asset filtering, search, and ordering
- persistent customer cart and cart item API foundation for market flows
- checkout and compatibility direct-order APIs for fixed-price order creation
- order validators and services for stock-validating fixed-price order creation
- seller item approval/rejection foundation
- seller-approval-time stock decrement for market order items
- seller-decision timeout auto-cancellation for stale pending market order items
- split asset/seller comments and customer asset/seller ratings as a simple trust layer
- nested rating comment creation with `is_rating_comment=True`
- cached rating aggregates on assets and seller profiles
- lightweight order events for future notification hooks
- auctions, auction items, bids, and events
- auction creation, bidding, limited update locking, cancellation, buy-now, and close/winner-resolution flows
- service / validator / factory-style pattern in auctions, although the create path currently constructs the auction in the view and uses the factory only for items
- Dockerized local and production runtime configuration
- Redis + Celery worker/beat infrastructure for background jobs
- automatic scheduled auction closing through Celery Beat

Current API documentation coverage:

- mounted DRF routers, APIViews, and manual `GenericViewSet.as_view(...)` endpoints appear in the schema
- custom actions in auctions, auth, manual cart/checkout ViewSets, and order-item approval/rejection have targeted schema annotations
- schema tests cover auth policy for `/api/schema/`, Bearer security output, cart/checkout paths, and representative custom/manual endpoints

The codebase does not yet fully implement:

- media/file subsystem
- payment-backed market completion workflow
- payment review workflow
- handoff flow
- ownership transfer workflow as a formal process
- dispute flow
- rating/comment moderation, replies, reports, likes, notifications, or analytics
- pedigree system
- pedigree reader
- QR flow
- race results
- points / cashback / badges
- offers / promotions
- chat
- notification delivery beyond lightweight order event placeholders
- seller package or quota enforcement as a finished business module

Important framing:

Current repo is a strong auction-oriented foundation, not a full product-complete backend yet.

Do not assume planned concepts already exist. Planned concepts must be labeled as planned, upcoming, or not yet implemented until code is present.

## Known Gaps / Known Risks

These risks are important and should be respected during future work.

### Auth / Permission Risks

- manager registration must not remain publicly open
- profile switching must respect activation and business constraints
- current `switch_user_profile()` can set an inactive/invalid/deleted target type active because it does not filter target profile validity before assignment
- current `build_token_for_user()` reads the first `is_active=True` profile row instead of the stricter `active_profile` property
- token `profile` claim and active profile behavior must stay aligned
- test JWTs must include `profile` unless testing missing-claim behavior
- permission constants and object-level rules need tightening
- current authorization behavior for managers, superusers, and JSON permissions is not yet fully intuitive
- `allow_scoped_read_access` can expose data if used without correct `get_queryset()` scoping
- `BirdViewSet` list visibility is not scoped even though object checks are owner-based
- `AuctionViewSet` visible list behavior and object-level retrieve behavior are not fully aligned because scoped safe reads are not enabled there

### Auction Risks

- bid validation must be correct and re-checked under DB lock
- event types must be consistent
- auction editing restrictions are partially enforced for `PATCH`, but default destroy still uses the base `ModelViewSet` path
- birds must not be reusable in conflicting active auctions
- auction creation currently checks overlapping active auctions but does not verify submitted bird ownership against the requesting seller
- regular bids require a customer profile, but buy-now currently lacks the same customer-profile type check
- same-user customer/seller profile bidding against that user's own seller auction is not explicitly blocked
- `Auction.is_active()` currently checks time only and does not include `Auction.status`
- manual `close_auction()` currently does not enforce `end_time <= now`; only the scheduled selector filters expired auctions
- `cancel_auction()` blocks `ended` and already-cancelled auctions, but does not explicitly block `closed`
- `AuctionFactory.create_auction()` is currently stale/unused and references non-model `auto_extend_threshold`
- permission constants include auction actions such as `publish` and `ended`, but no matching view actions currently exist
- settlement must enforce end-time and state rules before post-auction completion is added

### Business Flow Gap

Biggest current missing layer:

- post-auction completion workflow

Without this, auctions can end technically but not complete business-wise.

Important related gap:

- marketplace cart, checkout, order creation, and seller approval now exist, but payment review, handoff, settlement, and ownership transfer do not

### Media Gap

Current assets app is domain modeling, not file/media handling.

### Repo Cautions

- the service / validator / factory pattern is strongest in `apps.auctions`; `apps.users/services.py` is currently empty
- naming/style drift exists and should not be fixed opportunistically:
  - `apps/assets/serializer.py` is singular
  - `ManagerProfileadminSerializer` casing is inconsistent
  - several files contain TODOs or mixed comment quality
- auction service drift exists and should be fixed intentionally, not casually:
  - `AuctionFactory.create_auction()` references `auto_extend_threshold`, which is not a field on `Auction`
  - the live auction create endpoint constructs the `Auction` object in the view instead of using `AuctionFactory.create_auction()`
- ownership and soft-delete behavior are sometimes implicit in views and models; preserve existing semantics unless the task explicitly requires standardization
- product asset routes are currently plural under `/api/assets/`
- product asset tests should use project token builders, not raw SimpleJWT tokens
- API docs are schema metadata only; do not change business responses just to make docs prettier unless schema generation is otherwise incorrect
- current schema validation still emits benign enum-naming warnings for repeated `status` choice sets and the repeated profile choice set
- `.coverage` is removed from tracking; `.coverage` and `htmlcov/` are ignored local test artifacts
- pytest is part of the pre-commit workflow through the local `pytest -q` hook

## Planned Direction

The product direction below is planned and not fully implemented yet.

## Planned Phases

### Phase A - Core Auction Completion

Goal:

Make auctions complete end-to-end in business terms.

Includes:

- fix current correctness and security issues
- manual payment review
- seller handoff confirmation
- ownership transfer service
- minimal dispute flag
- extend auction event coverage

Priority:

This is the highest-priority phase.

### Phase B - Asset / Media Completion

Goal:

Enable real uploads and asset relations.

Includes:

- bird image support
- mandatory bird video support
- attachment support for future modules
- basic secure file handling
- storing file keys for objects uploaded by frontend to S3 or similar

Important note:

The assets app should remain a domain app, not a heavy media-processing pipeline.

### Phase C - Marketplace Core

Goal:

Complete fixed-price market flow on top of the current model foundation.

Includes:

- listing and market activation workflow beyond the current listing flag
- expansion of the current cart, checkout, and direct-order compatibility flow
- manual completion workflow similar to auctions
- payment review and seller handoff flow
- ownership transfer after manual confirmations
- stock adjustment rules for resolved order items
- stronger stock and quantity handling around completion

### Phase D - Trust Layer

Goal:

Expand the current ratings/comment trust layer.

Includes:

- moderation basics for existing comments and ratings
- richer review workflows if product scope requires them
- replies, reports, likes, or analytics only when explicitly prioritized

### Phase E - Bird Intelligence / Domain Enrichment

Goal:

Add bird-specific differentiated features.

Includes:

- race results
- pedigree data model
- pedigree export structure
- QR identity / transfer entrypoint
- pedigree reader later in a simple manual-assisted phase first

### Phase F - Engagement Layer

Goal:

Add growth and retention features.

Includes:

- points
- cashback as points, not money
- badges
- offers and promotions
- notifications
- chat later, kept simple first

## Immediate Next Sprint Direction

Current next sprint priority:

### Post-Auction Completion + Core Fixes

Required tasks:

1. fix critical auction/auth bugs already present
2. enforce auction locking/edit restrictions
3. add manual payment review entity and flow
4. add seller handoff confirmation flow
5. add ownership transfer service
6. add minimal dispute flag
7. extend auction events for post-auction lifecycle

Do not prioritize points, QR, chat, or promotions before this phase is complete.

## Payment Approach

Important project decision:

- no gateway integration
- no automated charge or capture
- no in-app financial processing
- admin manually confirms payment status

Planned expected flow:

1. auction winner is determined
2. buyer submits payment proof or info manually
3. admin approves or rejects payment
4. if approved, auction item moves forward in completion flow
5. seller confirms handoff
6. ownership is transferred

This project should behave as a marketplace coordination system, not a payment processor.

## Shipping / Handoff Approach

Important project decision:

There is no logistics provider integration and no shipping orchestration inside the app.

Planned expected flow:

- seller confirms that the item or bird has been handed over
- optional buyer confirmation may be introduced later
- system then completes ownership change

The platform records business confirmation states; it does not manage real shipping execution.

## Background Jobs & Time-Based Logic

### Celery Usage

Celery is now part of the current repo implementation.

Current implementation:

- `ppw/celery.py` initializes the Celery app
- `ppw/settings/base.py` configures broker, result backend, eager-mode test flags, order-item seller-decision timeout days, and beat schedule
- Docker Compose runs `redis`, `celery_worker`, and `celery_beat`
- the shared container image switches behavior through `APP_ROLE`

Rules:

- do not execute time-based or heavy operations inside the request/response cycle
- use Celery for:
  - auction lifecycle updates and auction settling
  - stale pending seller order-item cancellation
  - delayed or scheduled transitions
  - notifications later
  - non-critical async work
- all Celery tasks must be:
  - idempotent
  - safe to retry
  - side-effect controlled

Avoid:

- business logic hidden inside tasks
- tasks that are not retry-safe
- long blocking operations in views

### Auction Auto-Closing

Critical rule:

Auctions must automatically transition to `CLOSED` when `end_time` is reached.

This is not optional and must not rely on manual triggers.

Current implementation approach:

- Celery Beat runs `auctions.tasks.close_expired_auctions` every minute
- identify auctions where:
  - status is `active`
  - `end_time <= now`
- transition them to `CLOSED` via service layer
- `AuctionService.close_auction()` settles item winners and writes one `auction_ended` event
- `AuctionService.close_auction()` is idempotent for already `closed` auctions

Current caveat:

- the scheduled selector enforces expiry, but direct/manual calls to `close_auction()` do not currently reject early close attempts because the end-time check is commented out in the service

Rules:

- this transition must be idempotent
- it must use transaction safety
- it must not break if executed multiple times
- do not rely only on:
  - API calls
  - user actions
  - lazy evaluation in queries

All state transitions must go through explicit service methods, not direct model updates.

### Important Constraint

Time-based business logic must not depend on:

- user requests
- view execution
- serializer logic

It must be:

- deterministic
- background-driven
- centrally controlled

### Order Item Seller Timeout

Current implementation approach:

- `ORDER_ITEM_SELLER_DECISION_TIMEOUT_DAYS` defaults to `3`
- Celery Beat runs `orders.tasks.cancel_expired_pending_order_items` every 12 hours
- `OrderItemService.cancel_expired_pending_items()` selects `pending_seller` items whose `created` timestamp is older than the configured timeout
- each stale item is locked before it is moved to `cancelled`
- parent `Order.status` is refreshed after each item cancellation
- `seller_auto_cancelled` `OrderEvent` rows are written for the customer/order owner
- auto-cancellation never restores or decrements stock and never changes asset status or market listing state

Rules:

- seller timeout cancellation is per `OrderItem`, not per `Order`
- timeout starts from `OrderItem.created`
- task execution must remain idempotent and skip non-pending items

## Guidance For Future Codex Work

When working on this repo:

- understand first, then change
- plan briefly before coding when the change is non-trivial
- ask for approval when business rules, permission semantics, public API contracts, or destructive actions are ambiguous
- ask if business rules are ambiguous
- keep answers concise unless asked for detail
- prefer token efficiency
- implement the smallest correct solution
- follow existing conventions unless fixing a real issue
- separate critical issues from optional improvements
- treat post-auction completion as the current top business priority

If adding new modules:

- keep app boundaries clear
- add a new app only if the boundary is justified
- avoid premature abstractions
- add only the domain structures needed now
- leave room for later extension without overbuilding

## Testing Baseline

- auth tests: `pytest tests/authentication -q`
- asset tests: `pytest tests/assets -q`
- auction tests: `pytest tests/auctions -q`
- order tests: `pytest tests/orders -q`
- ratings tests: `pytest tests/ratings -q`
- API docs tests: `pytest tests/test_api_docs.py -q`
- full suite: `pytest -q`
- schema validation: `python manage.py spectacular --validate --settings ppw.settings.local --file /tmp/ppw-schema.yaml`
- pre-commit pytest hook runs `pytest -q`
- for future work, start with targeted tests in the touched app
- add broader regression tests only when changes cross module boundaries or alter shared behavior

## Default Assumptions For Future Work

- this file is both a current-state reference and a clearly labeled roadmap reference
- current implementation facts remain authoritative for present behavior
- roadmap items guide future implementation but are not implemented until the code exists
- existing behavior wins unless the user explicitly asks to change it
- when adding new backend workflows, prefer the auctions layering style as the default reference pattern

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
- stable public bird identity via UUID-backed QR payload URLs
- auction creation and bidding
- scheduled auction closing with winner resolution
- fixed-price product marketplace foundations through carts, checkout, orders, and seller item decisions
- admin-created marketplace discount and cashback offers for order items
- ratings and comments as a simple trust and feedback layer
- race records and race result rankings for pigeon/bird racing reports
- manual PaymentRequest-based auction and marketplace sale completion
- simple complaint/dispute records tied to approved or rejected payment requests
- customer-to-seller direct chat through a REST-only MVP
- internal in-app notification records and polling APIs
- customer follow relationships for sellers and public seller packages with deterministic home feed MVPs
- customer-to-seller block relationships that prevent new follow, feed, chat, and payment-request interactions
- seller home-page insights summaries from current marketplace data
- internal pedigree OCR extraction MVP with a null local OCR adapter
- registered FastReport pedigree ids with public or `UserAccount`-assigned private visibility
- Loft Manager subscriptions for pedigree template entitlement and AI quota access
- loyalty reward automation for selected profile, bid, listing, rating, follow, and seller-approved payment-request events
- ledger-backed Lucky Wheel attempts, backend-selected spins, and user-owned promotion prizes
- link-sharing referral codes with manager-approved referrals, inviter PP Coin rewards, and a monthly referral challenge (Top 10 leaderboard, frozen Top 3 prize winners)
- permanent buyer (`CustomerProfile`) and seller (`SellerProfile`) level tables (`100` / `500` / `1,000` / `5,000` / `10,000`) backed by `CompletedDeal` cumulative value (`BE-LTY-01A`, with automatic cross-currency threshold evaluation `LEVEL METRIC CURRENCY UNIT — BLOCKED` and all operational level benefits `BLOCKED` pending product rules; legacy `completed_orders_count` summary fields preserved on profile/feed serializers)
- reward seasons with seller insight snapshots and idempotent top-seller PP Coin awards
- future media, pedigree, expanded QR, reviews, expanded reward campaigns, realtime chat, and expanded promotion modules

PPW is currently a marketplace coordination platform.

It is not currently:

- a payment processor
- an escrow platform
- a wallet provider for buyer-seller marketplace sales
- a shipping or logistics platform
- a real-time social network
- a full media verification system

Current implementation is the source of truth for present behavior. If an original brief, roadmap note, or future prompt conflicts with current implementation or this `AGENTS.md` policy, follow current code and this guide unless the task explicitly asks for a product-policy change.

Core business reality and intended product policy:

- no in-app payment gateway
- no automated shipping or logistics integration
- payment is manual between buyer and seller outside the system
- buyer creates a `PaymentRequest` after a won auction item or approved market order item
- seller approves or rejects the `PaymentRequest`
- seller approval finalizes the sale inside the system
- bird ownership transfers on seller approval; non-bird product ownership does not transfer
- market product stock is reduced only when a seller approves a market `PaymentRequest`
- marketplace cashback is an internal customer balance only; it is not withdrawable cash

Current implementation caveat:

- there is no payment gateway, proof upload, admin payment approval, handoff model, shipping integration, wallet, refund engine, ownership history table, complaint chat, complaint attachments, automatic refund, ownership rollback, or cashback reversal workflow

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
- prefer DRF built-in viewset CRUD methods and standard hook names over custom CRUD wrappers
- use `perform_create`, `perform_update`, `perform_destroy`, serializer `create`, serializer `update`, and serializer `to_representation` before overriding full `create`, `update`, or `destroy`
- override full DRF CRUD methods only when the normal request / response flow itself must change
- do not add custom action names for behavior that is really a resource collection `list` or `create`; expose a proper route and map it to standard DRF action names instead
- declare endpoints in `urls.py`; do not declare endpoint paths through `@action`
- do not use `@action` for new work; use explicit `as_view({...})` mappings in the app URLconf
- do not use router-generated custom workflow endpoints for new work
- keep URLconfs readable: define small reusable action-map constants such as `LIST_ACTION`, `CREATE_ACTION`, and `PARTIAL_UPDATE_ACTION`, then declare each endpoint directly in `urlpatterns`
- avoid one-off view variables for every endpoint unless they are genuinely reused or improve clarity
- do not declare `head` or `options` as request methods in viewsets
- use standard DRF action method names only: `list`, `retrieve`, `create`, `update`, `partial_update`, and `destroy`
- prefer one focused viewset when endpoints share the same model/resource and action shape
- handle role-scoped list behavior in `get_queryset()` when manager/seller/customer filtering can stay clear there
- split one model/resource across multiple focused viewsets or view classes only when actors, serializers, source flows, or command behavior are meaningfully different
- do not create many tiny view classes for the same model when serializer selection and `get_queryset()` can keep one class readable
- command-style endpoints such as approval or rejection should use a dedicated URL-bound view mapped to `create`
- use `apps.payments.views` and `apps.payments.urls` as the current reference style for DRF viewset and URL organization
- API serializers own request payload field presence, blank-text handling, primitive choice validation, and conditional required-field validation
- services and validator classes should assume serializer-valid primitive API fields and should focus on business rules, persistence state, ownership, permissions, idempotency, locking, and side effects
- relation lookups and domain validity checks such as missing assets, missing packages, stock availability, seller ownership, and status transitions can remain in services or validators when they depend on current database state
- internal-only service safeguards can remain in services when no public serializer boundary exists, such as package usage-type support and point ledger invariants

Current internal API documentation approach:

- OpenAPI schema generation via drf-spectacular
- Swagger UI for local development
- targeted `@extend_schema` annotations only where automatic schema generation is weak
- document APIs and services close to the code with concise docstrings only when they clarify business intent, permissions, side effects, transactions, or non-obvious error cases
- do not add standalone API documentation files unless explicitly requested; `AGENTS.md` remains the living project guide
- avoid noisy comments that restate normal Django, DRF, serializer, queryset, or CRUD behavior
- when documenting endpoints, prefer the existing URLconf/OpenAPI annotations plus short view docstrings over large inline route inventories
- service docstrings should call out business rules, stock/ownership/payment side effects, idempotency, and locking only where those details affect safe maintenance
- suspected bugs or unclear business rules found during documentation work should be reported rather than silently fixed unless the task explicitly includes the fix

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
- `apps.packages`
- `apps.loyalty`
- `apps.lucky_wheel`
- `apps.notifications`
- `apps.payments`
- `apps.deals`
- `apps.loft_subscriptions`
- `apps.promotions`
- `apps.referrals`
- `apps.complaints`
- `apps.chat`
- `apps.feed`
- `apps.insights`
- `apps.pedigrees`
- `apps.seasons`
- `apps.draws`
- `apps.prizes`
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
  - `POST`: authenticated superuser-only protected manager creation path; it is no longer publicly usable and the main manager CRUD now lives under `/api/users/admin/managers/`
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
- `build_token_for_user()` uses the stricter `user.active_profile` property
- a token without `profile` is rejected with authentication failure
- if token `profile` differs from `request.user.role`, authentication attempts `switch_user_profile(user, new_profile_type=token_profile)`
- if profile switching fails, authentication continues with the current active profile
- `switch_user_profile()` returns a `(success, profile_type)` style result
- `switch_user_profile()` switches by profile type, not profile id
- `switch_user_profile()` refuses any switch from or to `Manager`
- `switch_user_profile()` filters target profiles by `activated=True`, `is_valid=True`, and `deleted__isnull=True` before setting `is_active=True`

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
- `/api/users/admin/managers/`
  - `GET`: authenticated superuser lists manager accounts; normal managers can list only with `managers.list`
  - `POST`: authenticated superuser creates a manager-only account with validated permissions
- `/api/users/admin/managers/<id>/`
  - `GET`: authenticated superuser retrieves one manager; normal managers can retrieve only with `managers.retrieve`
  - `PATCH`: authenticated superuser updates manager account/profile fields
  - `DELETE`: authenticated superuser deactivates the manager account and soft-deletes the manager profile
- `/api/users/admin/managers/<id>/permissions/`
  - `PATCH`: authenticated superuser replaces manager permissions with normalized validated codes
- `/api/users/admin/manager-permissions/`
  - `GET`: authenticated superuser lists the central manager permission registry
- `/api/users/admin/profile-balance-adjustments/`
  - `POST`: manager with `profile_balances.adjust` creates an idempotent customer/seller normal balance adjustment
- `/api/users/mobile/sellers/`
  - authenticated user-facing seller profile API
- `/api/users/mobile/customers/`
  - authenticated user-facing customer profile API
- `/api/seller-rooms/` and `/api/users/mobile/seller-rooms/`
  - `GET`: authenticated seller/customer room list (`?seller=<id>` or `?seller_id=<id>` filters public active rooms for a seller; sellers with no filter see their own non-deleted rooms)
  - `POST`: authenticated active seller creates a `SellerRoom` (`name`, optional `description`) owned by `request.user.active_profile`
- `/api/seller-rooms/<id>/` and `/api/users/mobile/seller-rooms/<id>/`
  - `GET`: authenticated seller/customer retrieves a public non-deleted seller room with `seller_id`, `seller_nickname`, `followers_count`, and `is_followed`
  - `PATCH` / `PUT`: owning seller updates `name` and/or `description`
  - `DELETE`: owning seller soft-deletes the room
- `/api/seller-rooms/<id>/follow/` and `/api/users/mobile/seller-rooms/<id>/follow/`
  - `POST`: authenticated non-manager `UserAccount` idempotently follows a specific `SellerRoom` (`SellerRoomFollow`)
- `/api/seller-rooms/<id>/unfollow/` and `/api/users/mobile/seller-rooms/<id>/unfollow/`
  - `POST`: authenticated non-manager `UserAccount` idempotently unfollows a specific `SellerRoom`

Current profile behavior:

- `active_profile` requires `is_active=True`, `is_valid=True`, `activated=True`, and a non-deleted profile
- `active_profile` raises `PermissionDenied` when no current valid active profile exists
- `active_profile` resolves the concrete subclass instance (`SellerProfile`, `CustomerProfile`, or `ManagerProfile`) before returning it
- `Profile.is_valid` is the current punishment / disabled-profile flag
- exactly one `is_active=True` profile per user is enforced through the active-profile uniqueness constraint
- persisted profile type values are title-case strings: `Seller`, `Customer`, and `Manager`
- `Profile.save()` derives `type` from the concrete subclass
- live manager accounts are exclusive: a user with a live `ManagerProfile` cannot create live seller/customer profiles, and a live seller/customer user cannot create a live `ManagerProfile`
- customer registration creates an active customer profile
- customer registration schedules best-effort `new_user_registered` notifications for active/activated/valid manager profiles after transaction commit
- manager creation keeps the account manager-only, creates an active/activated `ManagerProfile`, and sets `is_staff=True`
- `ManagerProfile.permissions` is the source of truth for manager authorization; legacy `UserAccount.permissions` has been removed from the runtime model
- all profile rows support optional private `national_id` and `address`; public feed/seller discovery serializers do not expose `national_id`
- customer and seller self-updates to locked identity/profile fields are limited to once every 60 days; admin profile updates bypass the lock but reset the affected user/profile waiting period
- locked identity fields are `username`, `phone_number`, `secondary_phone_number`, `email`, `first_name`, and `last_name`; locked profile fields are `nickname`, `country`, `currency`, `national_id`, `address`, `loft_name`, `loft_picture`, and `recoded_voice`
- `ProfileDataChangeLog` records self/admin data changes with changed fields and old/new values; system fields such as activation, validity, permissions, balances, and rating aggregates, as well as sensitive seller payment coordination fields (`bank_account`, `wallet`, `transfer_phone`), are not part of this lock/audit snapshot policy
- `SellerProfile` stores optional private off-platform payment coordination fields (`bank_account`, `wallet`, `transfer_phone`); these are editable only by the owning seller via `/api/users/mobile/sellers/<id>/`, are excluded from `SellerProfileAdminSerializer` and all public/discovery/feed/auction/payment/notification/prize serializers, never fall back to login `UserAccount.phone_number`, and are exposed to buyers only through the dedicated post-auction winner endpoint `GET /api/auctions/<id>/seller-payment-details/` (BE-PAY-PRIV-01)
- `SellerRoom` (`apps.users.models.SellerRoom`) represents a seller-owned auction room (`seller -> SellerProfile`, `name`, `description`) allowing one `SellerProfile` to manage multiple rooms under the same seller identity without duplicating seller profiles, wallets, ratings, or payment coordination fields; room-specific package tiers are currently `BLOCKED` pending product specification of room-level vs `SellerProfile`-level package ownership (BE-ROOM-01)
- `SellerRoomFollow` (`apps.users.models.SellerRoomFollow`) stores `UserAccount`-scoped room follows (`UniqueConstraint(fields=["room", "user"])`) so room follows survive `Customer` <-> `Seller` profile switching, remain independent from `SellerFollow`, and reject self-follows, manager accounts, and active customer-seller blocks (BE-ROOM-01)
- `ProfileBalanceTransaction` is the ledger for normal `CustomerProfile.balance` and `SellerProfile.balance` manager adjustments
- normal balance adjustments lock the target profile, reject zero amounts and negative resulting balances, and use unique idempotency keys
- manager permissions are centrally defined in `apps.users.manager_permissions` as a static action catalog, not generated module levels
- manager permission codes are exact action strings such as `assets.list`, `assets.update`, `users.sellers.retrieve`, `payments.list`, and `complaints.resolve`
- manager permissions do not have hierarchy; `assets.update` does not imply `assets.list`, and there is no current `edit` or `full_control` authorization behavior
- manager API access is deny-by-default unless the view declares a valid `manager_permission_map`, `manager_permission_namespace`, or `get_required_manager_permission()` result
- invalid or manually injected permission codes in `ManagerProfile.permissions` never authorize manager access
- superusers bypass manager permission checks and can use manager admin APIs without a `ManagerProfile`
- mobile seller profile creation creates an inactive seller profile and overwrites submitted `nickname` with `user.username`
- mobile customer/seller profile creation rejects manager accounts
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
- fuller media/file handling around the existing simple image reference model

Important note:

This app currently models domain assets and simple URL/reference-based media records, not a full media pipeline or media verification system.

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
  - `sale_channel`
  - `managed_by`
- `Asset.thumbnail_url` has been removed; asset media are represented by related `AssetMedia` rows
- current asset statuses are:
  - `available`
  - `sold`
  - `inactive`
- `Bird`, `Supply`, `Accessory`, `Feed`, and `Supplement` are subclasses of `Asset`
- asset `sale_channel` values are `seller_market` and `platform`; it records who operates/supplies the listing, is unrelated to `Order.source` (where a checkout came from), and must not be repurposed for order attribution
- `Supply` is platform-controlled: sellers cannot create supply products through the API
- platform supplies are owned by a hidden platform seller profile and use `sale_channel=platform`; manager/superuser creators are tracked through `managed_by` when an active manager profile exists
- `Bird.public_id` is a stable UUID for public bird identity and QR payload URLs
- `apps.general.models.Media` is a concrete base media-reference model with `media_url`, `media_type`, and optional `alt_text`
- `Media.media_type` currently supports `image` and `video`; existing and omitted media types default to `image`
- `apps.general.models.AssetMedia` is a concrete `Media` subclass linked to `Asset` through `asset.media`
- `AssetMedia` adds `is_primary` and `order`; ordering is by `order`, then `created`
- media upload processing, S3 upload orchestration, FFmpeg, watermarking, anti-fraud verification, mandatory controlled in-app video capture, minimum image-count validation, and MIME scanning are not implemented in the current MVP

Current bird API:

- `/api/assets/birds/`
- `/api/assets/birds/public/<public_id>/`

Current bird API behavior:

- `BirdViewSet` is a DRF `ModelViewSet`
- `BirdSerializer` sets `owner` from `request.user.active_profile` on create
- `owner` is read-only in API input
- `public_id` is read-only in API input and must not be confused with internal `Bird.id`
- `BirdSerializer` exposes `qr_payload_url` built from `PUBLIC_FRONTEND_BASE_URL` as `{PUBLIC_FRONTEND_BASE_URL}/birds/{public_id}`
- `BirdSerializer` returns nested `media` and accepts nested `media` on create
- `BirdSerializer` replaces the full media set when `media` is supplied on update; omitted `media` preserves existing media rows and `media: []` clears them
- `BirdSerializer` exposes FE-safe read-only `seller_nickname`, `rating`, and `review_count`; list querysets annotate rating aggregates to avoid N+1 reads
- the public bird endpoint is unauthenticated, looks up by `public_id`, and returns `PublicBirdSerializer`
- the public bird endpoint excludes deleted and inactive birds
- `PublicBirdSerializer` exposes only public-safe bird identity fields, media, and seller display info; it does not expose internal DB ids, owner ids, prices, admin fields, phone, or email
- the backend does not generate, store, or return QR images; frontend clients render QR codes from `qr_payload_url`
- `images` and `image_url` are no longer accepted asset media API field names
- `thumbnail_url` is no longer accepted in bird API payloads
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
- product asset serializers return nested `media` and accept nested `media` on create
- product asset serializers replace the full media set when `media` is supplied on update; omitted `media` preserves existing media rows and `media: []` clears them
- product asset serializers expose FE-safe read-only `rating` and `review_count`; list querysets annotate rating aggregates to avoid N+1 reads
- `images` and `image_url` are no longer accepted product media API field names
- `thumbnail_url` is no longer accepted in product asset API payloads
- create sets `owner` from `request.user.active_profile` for seller-created accessory, feed, and supplement assets
- seller product creation goes through `ProductAssetCreationService.create_product()`
- seller-created `Accessory`, `Feed`, and `Supplement` assets must include `seller_package_id` and consume `Package.product_cost` from that selected active seller package, even when `is_market_listed=False`
- seller-created product assets store the selected package on nullable `Asset.seller_package` for historical traceability
- supply creation goes through the platform supply branch, sets the hidden platform seller profile as owner, sets `sale_channel=platform`, and does not consume seller package points
- managers manage supplies through `platform_products.list`, `platform_products.retrieve`, `platform_products.create`, `platform_products.update`, and `platform_products.delete`; generic `assets.create` must not create platform supplies
- manager-created accessory, feed, and supplement assets are not part of the current manager API contract
- product edits, listing changes, price/count/status/title changes, and deletes do not consume package points
- `BirdSerializer` and bird creation do not consume package points
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
- seller-decision timeout auto-cancellation for stale pending order items
- lightweight order event records for future notification hooks

Important note:

This app provides the cart, checkout, and seller item-decision layer. Manual sale completion after seller approval is owned by `apps.payments`.

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
  - `POST`: customer converts the full active cart into an order after validating stock availability; optional `source` (`mobile` | `web`) records the checkout client, omitted means `unknown`, and any other value (including `unknown`) is rejected
- `/api/orders/`
  - `GET`: paginated customer list of own orders or manager list of all orders; supports exact `status` filtering
  - `POST`: compatibility alias for direct order creation from market-listed seller assets; validates stock availability without decrementing stock; accepts the same optional `source` as checkout
- `/api/orders/<id>/`
  - `GET`: customer retrieves own order; manager retrieves any order
- `/api/orders/items/`
  - `GET`: paginated seller list of own order items or manager list of all order items; supports `status`, `discount_offer=<id>`, and `cashback_offer=<id>` filters
- `/api/orders/items/<id>/`
  - `GET`: seller retrieves their own seller-side order item; manager retrieves any order item with `order_items.retrieve`
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
- order item detail asset summaries use nested `media` from `AssetMedia`; they no longer expose `thumbnail_url`
- checkout and direct `POST /api/orders/` validate stock but do not decrement `Asset.count`
- checkout and direct `POST /api/orders/` do not mark assets `sold` and do not unlist assets from the market
- `Order.source` (`mobile`, `web`, `unknown`) is the historical checkout origin for attribution/analytics only: it is set once when the order is created, is read-only in every order response, has no update API, and never affects price, promotions, stock, seller approval, payment requests, or `CompletedDeal`; client-declared values are accepted because nothing depends on them
- orders created before `Order.source` existed, and internal/service-created orders without a source, are `unknown`; historical origin is not reconstructed
- auctions do not create `Order` rows, so auction sales carry no `Order.source`
- checkout and direct `POST /api/orders/` calculate `gross_total`, apply the best active `DiscountOffer`, store `discount_offer` and `discount_amount`, and store discounted `total_price`
- checkout and direct `POST /api/orders/` do not redeem or earn cashback
- checkout and direct `POST /api/orders/` create best-effort in-app notifications for each seller with a pending order item
- new order items start as `pending_seller`
- seller approval changes `pending_seller` to `approved` without decrementing stock, marking assets sold, or unlisting assets
- seller approval checks active `CashbackOffer` eligibility, stores `cashback_offer`, `cashback_redeemed_amount`, and `cashback_earned_amount`, and updates `OrderItem.total_price`
- seller approval redeems cashback from `CustomerProfile.cashback_balance` only through `CashbackService` using `transaction.atomic()`, `select_for_update()`, and a `CashbackTransaction`
- seller approval does not revalidate stock at approval time
- seller approval is idempotent when the item is already `approved`
- seller rejection changes `pending_seller` to `rejected` only; it does not change stock, asset status, or market listing
- seller rejection is idempotent when the item is already `rejected`
- seller approval and rejection create best-effort in-app notifications for the customer
- pending seller order items older than `ORDER_ITEM_SELLER_DECISION_TIMEOUT_DAYS` are automatically moved to `cancelled` by a Celery task
- auto-cancelling a pending order item refreshes the parent order status but does not change stock, asset status, or market listing
- auto-cancelling a pending order item creates a best-effort in-app notification for the customer
- aggregate order statuses are `pending`, `processing`, `completed`, `cancelled`, and `partial_rejected`
- if all order items are rejected or cancelled, the aggregate order status becomes `cancelled`
- if all order items are completed, the aggregate order status becomes `completed`
- approved, awaiting-payment-review, ready-handoff, or completed items can move the aggregate order status to `processing`
- any rejected item plus any non-rejected item makes the aggregate order status `partial_rejected`
- lightweight `OrderEvent` records are created for order creation, seller pending item notifications, seller approval, and seller timeout auto-cancellation
- seller rejection currently does not create an `OrderEvent`
- approved marketplace order items can be finalized by buyer-created `PaymentRequest` records in `apps.payments`

Current order service implementation names:

- `OrderCheckoutService._create_pending_order()` creates the order and pending seller items for cart checkout and direct order creation
- `OrderCheckoutService._ensure_stock_available()` validates count during checkout/order creation without mutating assets; purchase validators enforce listed/status/seller/customer rules before this helper runs
- `OrderItemService.approve_item()` approves seller-side order items without stock mutation and delegates cashback redemption to `CashbackService.redeem_for_order_item()`
- `OrderItemService.cancel_expired_pending_items()` cancels stale `pending_seller` order items whose `created` timestamp is outside the configured seller decision window

### `apps.payments`

Current responsibilities:

- shared manual `PaymentRequest` base records for won auction items and approved marketplace order items
- concrete `AuctionPaymentRequest` and `MarketPaymentRequest` submodels for source-specific relations
- buyer-created manual sale confirmation requests
- seller approval or rejection of pending payment requests
- seller approval finalization for marketplace order items
- market stock reduction when a market payment request is approved
- bird ownership transfer on seller-approved payment requests
- lightweight `PaymentRequestEvent` trace records

Important note:

This app is not a payment gateway, Apple Pay / Google Pay / card-processing integration, escrow system, wallet, refund system, proof-upload flow, admin payment-approval flow, handoff model, shipping integration, or complaint/dispute system. It records manual buyer/seller confirmation state only. Complaint/dispute records are owned by `apps.complaints`.

Current payment API:

- `/api/payments/requests/`
  - `GET`: manager lists all payment requests; seller lists assigned requests; customer lists own requests
- `/api/payments/requests/auction-item/`
  - `POST`: customer creates a payment request for a sold auction item they won
- `/api/payments/requests/order-item/`
  - `POST`: customer creates a payment request for their approved marketplace order item
- `/api/payments/requests/<id>/`
  - `PATCH`: buyer updates `buyer_note` while the request is pending or rejected
- `/api/payments/requests/<id>/approve/`
  - `POST`: seller approves their own pending payment request
- `/api/payments/requests/<id>/reject/`
  - `POST`: seller rejects their own pending payment request with optional `seller_note`

Current payment behavior:

- payment request statuses are `pending`, `approved`, `rejected`, and `cancelled`
- payment request types are `auction` and `market`
- `PaymentRequest` is a concrete shared base model
- `AuctionPaymentRequest` stores the required one-to-one `auction_item`
- `MarketPaymentRequest` stores the required one-to-one `order_item`
- auction payment requests require a sold `AuctionItem`, the active customer profile must be the winner, and the auction owner must be a seller profile
- auction payment request creation is rejected when an active `SellerCustomerBlock` exists between the winning customer and auction seller
- auction payment request amount comes from `AuctionItem.current_price`
- seller approval finalizes the internal auction sale and transfers the sold bird ownership; an auction item's `paired_bird` also transfers when present
- marketplace payment requests require an approved `OrderItem` owned by the active customer profile
- marketplace payment request creation is rejected when an active `SellerCustomerBlock` exists between the order customer and item seller
- marketplace payment request amount comes from `OrderItem.total_price`
- only one pending or rejected request per buyer and asset is allowed at a time
- buyers can update pending or rejected requests only; updating a rejected request resubmits it as pending
- approved and cancelled payment requests cannot be updated through the service
- sellers can approve or reject only their own pending payment requests
- seller approval marks the request approved and immutable
- seller approval is the current MVP sale-completion signal; there is no separate shipping, handoff, delivery, buyer receipt confirmation, payment-proof, or admin payment approval step in the normal payment flow
- seller approval completes the source marketplace `OrderItem` and refreshes the parent order status
- seller approval for market requests locks the source asset, requires enough current stock, decrements `Asset.count`, and marks zero-count assets `sold` and unlisted
- seller approval for market requests credits earned marketplace cashback once through `CashbackService.earn_for_completed_order_item()` after the source `OrderItem` is completed
- seller approval transfers ownership only for bird assets
- seller approval transfers an auction item's `paired_bird` when present
- seller approval does not transfer ownership for supply, accessory, feed, or supplement assets
- seller rejection marks the request rejected and does not transfer ownership, complete order items, or mutate stock
- `PaymentRequestEvent` records are written only for created, updated, approved, and rejected events
- seller approval records exactly one `apps.deals.CompletedDeal` for the order item or auction item inside the same approval transaction, after finalization; recording the deal emits the `DEAL_COMPLETED` reward event, so `apps.payments` no longer calls the reward engine

### `apps.deals`

Current responsibilities:

- `CompletedDeal`: the canonical, immutable record that a buyer/seller deal completed (`auction` or `market`)
- `CompletedDealService.create_from_market_completion()` / `create_from_auction_completion()` and buyer/seller query helpers

Important note:

`CompletedDeal` is business history, not a payment or proof of electronic payment. `PaymentRequest` stays the manual confirmation workflow; its seller approval is the current input that completes a deal. There is no API; Django admin is read-only.

Current behavior:

- the source is the business unit (`order_item` or `auction_item` + id), unique per deal at the database level; the confirming `payment_request_id` is kept in `metadata`
- buyer/seller are stored as both account (`UserAccount`) and mode (`Profile`); each profile must belong to its account
- `amount` snapshots `OrderItem.total_price` or `AuctionItem.current_price`; `currency` snapshots the seller profile currency; `completed_at` is the confirmation approval time
- creation is idempotent and rolls back with the approval transaction; the model refuses updates and deletes
- deals approved before this app existed have no `CompletedDeal` (no backfill yet)

### `apps.loft_subscriptions`

Current responsibilities:

- Loft Manager subscription catalog records and admin-managed pricing
- backend-authoritative subscription pricing, quote, checkout, and invoice snapshots
- one-time per-`UserAccount` trial usage tracking
- manual trusted-manager subscription payment confirmation
- profile-owned subscription entitlement snapshots for pedigree template access and add-ons
- idempotent AI quota consumption records for subscription-backed AI operations

Important note:

This app is not a payment gateway, recurring billing system, coupon engine, tax engine, refund/proration engine, cloud-storage provider, QR generation workflow, custom template design workflow, or external pedigree report provider. It records subscription invoices and trusted backend confirmation only. Auction and marketplace sale-completion payment requests remain owned by `apps.payments`.

Current Loft Manager subscription API:

- `/api/loft-subscriptions/catalog/`
  - `GET`: authenticated users read only active purchasable catalog records
- `/api/loft-subscriptions/revise/`
  - `POST`: dry-run pricing for selected duration, template package, add-ons, optional AI package, and provider PDF ids; creates no rows
- `/api/loft-subscriptions/checkout/`
  - `POST`: validates a signed quote, recalculates current pricing, creates an invoice and lines, creates a subscription payment request for paid invoices, and directly activates a valid free trial
- `/api/loft-subscriptions/current/`
  - `GET`: reads the active profile's current active subscription and AI balance summary
- `/api/loft-subscriptions/invoices/`
  - `GET`: owner lists own invoices; managers list all with `loft_subscriptions.invoices.list`
- `/api/loft-subscriptions/invoices/<id>/`
  - `GET`: owner retrieves own invoice; managers retrieve any with `loft_subscriptions.invoices.retrieve`
- `/api/loft-subscriptions/payment-requests/`
  - `GET`: owner lists own subscription payment requests; managers list all with `loft_subscriptions.payment_requests.list`
- `/api/loft-subscriptions/payment-requests/<id>/`
  - `GET`: owner retrieves own subscription payment request; managers retrieve any with `loft_subscriptions.payment_requests.retrieve`
- `/api/loft-subscriptions/payment-requests/<id>/confirm/`
  - `POST`: manager with `loft_subscriptions.payment_requests.confirm` marks a subscription payment request confirmed and activates or schedules the subscription

Current Loft Manager subscription behavior:

- catalog prices are stored in database rows and all public pricing uses `SubscriptionPricingService.calculate()`; views and serializers must not hardcode prices or trust frontend totals
- the frontend sends selected object ids only: `duration_id`, `pedigree_package_id`, `addon_ids`, optional `ai_package_id`, and `template_ids`
- quote tokens use Django signing, include selected ids, currency, total, and issue time, expire after 10 minutes, and are only a stale-price guard because checkout always recalculates
- checkout idempotency uses `SubscriptionInvoice.idempotency_key`; retrying the same selection returns the existing invoice, while conflicting reuse is rejected
- invoice lines, selected template ids, add-on codes, selected catalog ids, prices, and entitlement snapshots are historical snapshots; paid invoices must not be displayed from current catalog names or prices
- paid checkout creates a pending invoice and one `SubscriptionPaymentRequest`; it never activates the subscription until trusted confirmation
- manager confirmation marks the subscription invoice paid and creates exactly one subscription; repeated confirmation returns the existing result safely
- when no active subscription exists, a paid subscription starts at confirmation time; when an active subscription still has time, the new subscription is created as `scheduled` and starts at the active subscription's `ends_at`
- paid durations use calendar-month arithmetic through `dateutil.relativedelta`; trials use exactly three calendar days
- the free trial is limited to one use per `UserAccount`, activates directly, lasts three days, requires the one-shape package and exactly one template id, includes 10 total AI operations, and cannot include paid add-ons or a paid AI package
- selected provider PDF ids are stored as positive integers; pricing and checkout never call the external FastReport provider
- limited packages require at least one selected template id, `shapes_1` requires exactly one, other limited packages allow up to their limit, and unlimited packages allow any number of positive ids
- active subscriptions store entitlement snapshots: template limit/unlimited flag, selected template ids, add-on codes, included AI operations per month, and purchased AI operations
- `SubscriptionAccessService.require_pedigree_template()` gates `/api/pedigrees/generate/` after the registered-pedigree visibility check and local subject access validation, and before the external provider call
- `SubscriptionAccessService.require_addon(profile, "medical_records")` is the future medical-record endpoint gate; no medicine endpoint currently exists
- `SubscriptionAccessService.require_addon(profile, "qr_code_pedigree")` is the future QR-generation endpoint gate; the current public asset QR payload URL is unchanged
- Cloud Backup is exposed only as an entitlement code for now; no cloud-storage provider is implemented
- AI quota consumption uses `SubscriptionAIQuotaService.consume(profile, operation_code, idempotency_key)` and should be called only after an actual AI-backed operation succeeds; current local OCR, review, retrieval, and PDF generation do not consume AI quota
- paid subscriptions consume the current monthly included allowance first and purchased operations second; included monthly operations do not roll over, and purchased operations expire with the subscription
- trial AI quota is 10 total operations for the whole trial and does not monthly reset

### `apps.complaints`

Current responsibilities:

- simple complaint/dispute records tied to `PaymentRequest`
- buyer/seller complaint creation after a payment request is approved or rejected
- manager review transitions for open complaints
- manager reopen transitions for resolved or rejected complaints
- creator cancellation of open complaints
- lightweight `ComplaintEvent` trace records

Important note:

This app is not a refund system, ownership rollback system, stock reversal system, cashback reversal system, complaint chat, attachment upload flow, legal arbitration workflow, notification system, seller penalty system, or payment approval tool. It records manual dispute review state only.

Current complaint API:

- `/api/complaints/`
  - `GET`: manager lists all complaints
  - `POST`: related buyer or seller creates a complaint for an approved or rejected payment request
- `/api/complaints/my/`
  - `GET`: buyer or seller lists complaints related to their active profile
- `/api/complaints/<id>/`
  - `GET`: manager retrieves any complaint; buyer or seller retrieves related complaints only
- `/api/complaints/<id>/cancel/`
  - `POST`: complaint creator cancels an open complaint
- `/api/complaints/<id>/mark-in-review/`
  - `POST`: manager moves an open complaint to review
- `/api/complaints/<id>/resolve/`
  - `POST`: manager resolves an open or in-review complaint with optional `admin_note`
- `/api/complaints/<id>/reject/`
  - `POST`: manager rejects an open or in-review complaint with optional `admin_note`
- `/api/complaints/<id>/reopen/`
  - `POST`: manager reopens a resolved or rejected complaint

Current complaint behavior:

- complaints can be created only for `PaymentRequest.status` values `approved` and `rejected`
- complaints cannot be created for `pending` or `cancelled` payment requests
- only the payment request buyer or seller can create a complaint
- `Complaint.buyer` and `Complaint.seller` are copied from the payment request at creation time
- default complaint type is `post_sale` for approved payment requests and `payment_rejected` for rejected payment requests
- complaint `title`, `description`, and `complaint_type` API field validation is owned by `ComplaintCreateSerializer`
- complaint status values are `open`, `in_review`, `resolved`, `rejected`, and `cancelled`
- one active complaint per payment request is enforced by service validation and a partial DB unique constraint over `open` and `in_review`
- closed complaints do not block a later new active complaint for the same payment request
- managers can mark open complaints `in_review`
- managers can resolve or reject open and in-review complaints
- managers with `complaints.reopen` can reopen resolved or rejected complaints; reopening sets `status=open`, clears `admin_note`, clears `resolved_at`, and writes a `complaint_reopened` event
- open and in-review complaints cannot be reopened
- only the complaint creator can cancel, and only while the complaint is `open`
- complaint actions do not mutate `PaymentRequest`, asset ownership, product stock, orders, cashback, or ratings
- complaint actions do not refund money, roll back ownership, reverse stock, reverse cashback, mutate approved payment requests, or delete orders, conversations, or messages
- `ComplaintEvent` records are written for created, in-review, resolved, rejected, reopened, and cancelled transitions

Current complaint service implementation names:

- `ComplaintService.create_complaint()` creates an open complaint after locking and validating the payment request
- `ComplaintService.mark_in_review()` moves an open complaint into manager review
- `ComplaintService.resolve()` resolves an open or in-review complaint
- `ComplaintService.reject()` rejects an open or in-review complaint
- `ComplaintService.reopen_complaint()` reopens a resolved or rejected complaint without payment, ownership, stock, order, cashback, or rating side effects
- `ComplaintService.cancel()` cancels an open complaint created by the active profile

### `apps.chat`

Current responsibilities:

- direct one-to-one conversations between one customer profile and one seller profile
- REST-only conversation creation and listing
- REST-only message listing and text message sending
- message read marking for the active receiver
- simple contact-sharing rejection for MVP chat safety
- Celery notification task placeholder for new chat messages

Important note:

This app is not a realtime system. It does not implement WebSockets, Firebase, realtime database behavior, typing indicators, online presence, group chat, public auction chat, attachments, asset/order-linked conversations, moderation UI, message deletion, or frontend behavior.

Current chat API:

- `/api/chat/conversations/`
  - `GET`: authenticated active customer or seller profile lists only conversations where it is the customer or seller
  - `POST`: authenticated active profile creates or retrieves the existing conversation with `receiver_profile_id`
- `/api/chat/conversations/<id>/messages/`
  - `GET`: participant lists messages in one conversation; supports optional `after_id`
  - `POST`: participant sends a new text message
- `/api/chat/conversations/<id>/mark-read/`
  - `POST`: participant marks unread messages addressed to their active profile as read

Current chat behavior:

- conversations are tied to profiles, not directly to `UserAccount`
- `Conversation.customer` points to `CustomerProfile`
- `Conversation.seller` points to `SellerProfile`
- only one conversation can exist for a customer and seller pair
- existing conversations are returned instead of creating duplicates
- active profiles cannot create conversations with themselves
- conversation creation rejects customer/customer, seller/seller, manager/customer, and manager/seller pairs
- conversation creation rejects customer and seller profiles that belong to the same user account
- conversation creation rejects customer/seller pairs with an active `SellerCustomerBlock`
- conversation creation rejects receiver profiles that are not active, activated, valid, or non-deleted
- when a customer starts a new conversation with a seller, the customer must actively follow that seller first; existing conversations can still be returned even if the follow is later inactive
- sellers can start or reply in existing customer-seller conversations without requiring the customer follow gate at send time
- conversation list is scoped to the active customer or seller profile only
- conversation list uses `Conversation.last_message` and `last_message_at` for latest-message summary and ordering
- conversation list exposes `unread_count` for unread messages addressed to the requesting active profile
- message list is scoped to the conversation customer or seller only and orders by `created`, then `id`
- message list supports `after_id` for polling-style incremental fetches
- sending requires an active conversation and a sender who is the conversation customer or seller
- sending rejects new messages when an active `SellerCustomerBlock` exists between the conversation customer and seller
- message receiver is derived as the other participant; clients do not submit receiver ids
- messages are text-only and capped at `Message.MAX_BODY_LENGTH`
- empty messages are rejected
- messages containing phone-like numbers, email addresses, URLs, WhatsApp references, `wa.me`, Telegram, or `t.me` references are rejected
- sending a message updates `Conversation.last_message` and `last_message_at`
- sending a message triggers `send_chat_message_notification(message_id)` through Celery
- notification enqueue failures are logged and do not roll back message creation
- the chat notification task creates an in-app `chat_message_received` notification through `apps.notifications`; enqueue or notification creation failures do not roll back message creation

Current chat service implementation names:

- `conversation_queryset()` returns optimized conversation rows with customer, seller, and last-message relations
- `message_queryset()` returns optimized message rows with sender and receiver relations
- `ChatService.get_or_create_conversation()` resolves the customer/seller pair, returns existing conversations, and requires an active follow before a customer can start a new seller conversation
- `ChatService.send_message()` validates customer/seller participation, state, and contact-sharing rules, creates the message, updates conversation summary fields, and enqueues notification
- `ChatService.mark_messages_read()` marks unread messages for the reader profile as read
- `send_chat_message_notification()` is the Celery notification placeholder task

### `apps.notifications`

Current responsibilities:

- internal in-app notification records (`Notification`)
- notification read/unread state
- polling APIs for notification list and unread count
- read-marking commands
- post-commit WebSocket realtime broadcast (`broadcast_notification`) and Celery/FCM mobile push delivery (`deliver_notification_push`, `PushDevice`)
- account-wide Do Not Disturb (DND) preference (`NotificationPreference`, `NotificationPreferenceService`) that suppresses mobile FCM push delivery only during one configured daily interval (BE-NOT-02A)
- idempotent notification creation helpers for other business modules

Important note:

This app owns in-app notifications, post-commit WebSocket realtime delivery, FCM push delivery, and account-wide DND push suppression. It does not implement email, SMS, WhatsApp delivery, notification digests, delayed/catch-up push scheduling, per-category notification preferences, per-profile/per-device DND, or admin broadcast workflows.

Current notifications API:

- `/api/notifications/`
  - `GET`: authenticated active profile lists only its own notifications with optional `is_read` and `type` filters
  - `POST`: manager with `notifications.create` creates one manual in-app notification for a selected active recipient profile; broadcast is not implemented
- `/api/notifications/preferences/`
  - `GET`: authenticated user retrieves their `UserAccount`-owned DND settings (`dnd_enabled`, `dnd_start`, `dnd_end`; defaults to `{"dnd_enabled": false, "dnd_start": null, "dnd_end": null}` when no row exists)
  - `PATCH`: authenticated user updates their `UserAccount`-owned DND settings (`dnd_enabled`, `dnd_start`, `dnd_end`); rejects client-supplied `user`, `user_id`, `profile`, or `profile_id`
- `/api/notifications/unread-count/`
  - `GET`: authenticated active profile gets unread notification count
- `/api/notifications/<id>/mark-read/`
  - `POST`: authenticated active profile marks one of its own notifications as read
- `/api/notifications/mark-all-read/`
  - `POST`: authenticated active profile marks all of its unread notifications as read
- `/api/notifications/devices/`
  - `GET` / `POST`: authenticated user lists or registers active `PushDevice` rows (`token`, `platform`) for the current active profile
- `/api/notifications/devices/<id>/`
  - `DELETE`: authenticated user deactivates one of their active profile's `PushDevice` rows

No notification delete endpoint is currently implemented.

Current notifications behavior:

- `Notification.recipient_profile` points to the active recipient `Profile`
- `Notification.actor_profile` is optional and stores the profile that caused the business event when available
- `Notification.metadata` stores small related-object ids such as `order_id`, `order_item_id`, `asset_id`, `payment_request_id`, `auction_id`, `auction_item_id`, `bid_id`, `conversation_id`, `message_id`, and `badge_award_id` when a trigger has them
- notification ordering is newest first by `created`, then `id`
- read state is tracked by `is_read` and optional `read_at`
- `idempotency_key` prevents duplicate notifications for the same business event when supplied
- inactive, invalid, unactivated, or deleted recipient profiles do not receive newly created notifications
- mark-read is idempotent and only the recipient profile can mark one notification as read
- mark-all-read uses a bulk update over the current active profile's unread rows
- `NotificationService.create_notification()` creates the `Notification` DB row inside the active transaction and schedules WebSocket broadcast (`broadcast_notification`) and Celery/FCM push (`deliver_notification_push.delay`) strictly via `transaction.on_commit()`
- `NotificationService.safe_create_notification()` is the best-effort helper for business integrations; notification failures are logged and must not roll back the main business transaction
- `NotificationPreference` (`OneToOneField` to `UserAccount`) stores account-wide `dnd_enabled` (default `False`), `dnd_start` (`TimeField`, nullable), and `dnd_end` (`TimeField`, nullable) for one optional daily DND interval (supporting both same-day e.g. `13:00 -> 15:00` and overnight e.g. `23:00 -> 08:00` windows); rows are created lazily on first `PATCH` with no historical backfill, and switching between `Customer` and `Seller` profiles preserves the same DND settings (BE-NOT-02A)
- during an active DND window, only mobile FCM push delivery (`deliver_notification_push` / `send_notification_to_devices`) is suppressed immediately (not delayed or queued for when DND ends); the `Notification` DB row is still created and visible in-app (`is_read=False` unchanged), and in-app WebSocket realtime broadcast (`broadcast_notification`) still runs normally (BE-NOT-02A)
- current safe integrations create in-app notifications for:
  - received chat messages
  - payment request created, approved, and rejected
  - checkout-created pending seller order items
  - seller order-item approved and rejected
  - seller decision timeout auto-cancelled order items
  - new auction bids to the auction owner
  - auction won
  - auction lost
  - auction reserve not met
  - buy-now success to the buyer
  - new customer registration to active managers
  - complaint status updated
  - manual manager notifications
  - PP Coins earned (`points_earned`, BE-NOT-01 & BE-NOT-02A)
  - badge awarded (`badge_awarded`, BE-NOT-01)
  - claim-required prize awarded (`prize_awarded`, BE-NOT-01)
- `points_earned` congratulatory notifications are created atomically inside `PointService._create_transaction()` only when a new positive `PointTransaction` (`transaction_type=earn`, `points > 0`) is created (`idempotency_key=f"points_earned:{point_tx.id}"`), using short, localized source-specific copy when `source_type` identifies onboarding, referral, completed deal, season winner, or Lucky Wheel (`PointService._build_points_earned_body`, BE-NOT-01 & BE-NOT-02A); duplicate idempotent `earn_points()` calls, `spend_points()`, and `expire_points()` create `0` notifications
- `badge_awarded` congratulatory notifications are created atomically inside `BadgeService.award_badge()` only when a new `BadgeAward` row (`created_new=True`) is created (`idempotency_key=f"badge_awarded:{award.id}"`); returning an existing idempotent award does not create another notification
- `prize_awarded` congratulatory notifications are created atomically inside `PrizeAwardService._record_award()` only when a new `CLAIM_REQUIRED` `PrizeAward` (`created_new=True`) is created (`idempotency_key=f"prize_awarded:{award.id}"`); `PrizeAwardService.record_digital_fulfilled()` never creates a generic prize notification so digital PP Coin and badge prizes do not duplicate their `points_earned` or `badge_awarded` notifications, and the cancelled weekly onboarding draw (`BE-DRW-CANCEL-01`) creates `0` notifications
- because `Notification` is owned by `recipient_profile = ForeignKey("users.Profile")` and `/api/notifications/` filters by `request.user.active_profile.pk`, account-level reward/prize notifications (`ACCOUNT_ONBOARDING`, `FIRST_BID_COMPLETED`, `REFERRAL_CHALLENGE` `PRIZE_AWARDED`) are routed to the user's currently active valid profile at creation time and are visible only while that profile is active; cross-profile in-app notification history is intentionally not redesigned in `BE-NOT-02A`
- notification grouping/digests and per-category enable/disable preferences are not implemented in `BE-NOT-02A`
- current notification types also reserve room for future cashback-earned, package-expiring-soon, auction-outbid, and complaint-created notifications, but those are not wired yet


### `apps.feed`

Current responsibilities:

- customer-to-seller follow relationships
- customer-to-public-seller-package follow relationships
- customer-to-seller active block relationships
- idempotent seller follow and unfollow endpoints
- idempotent seller package follow and unfollow endpoints
- customer/seller block, unblock, and active block list endpoints
- active customer following-list endpoint
- active customer seller-package following-list endpoint
- random active seller suggestions for customers
- cursor-based customer auction feed
- cursor-based customer market feed
- deterministic discovery fallback after followed seller/package content

Important note:

This app is named `feed`, not `social`. It is not a recommendation engine, notification system, realtime feed, seen-item tracker, ads/sponsored-content system, separate seller-room model, frontend module, or public unauthenticated discovery feed.

Current feed API:

- `/api/feed/sellers/`
  - `GET`: authenticated paginated active seller discovery list; excludes the requesting user's own seller profile and active customer/seller blocks where applicable; returns public-safe seller fields including `avatar_url` and the seller's active non-deleted `rooms`
- `/api/feed/sellers/<seller_id>/`
  - `GET`: authenticated public-safe seller discovery detail including the seller's active non-deleted `rooms` (`SellerRoomPublicSerializer`)
- `/api/feed/sellers/<seller_id>/follow/`
  - `POST`: authenticated active customer creates or reactivates a follow for an active seller
- `/api/feed/sellers/<seller_id>/unfollow/`
  - `POST`: authenticated active customer deactivates a follow if present
- `/api/feed/sellers/following/`
  - `GET`: authenticated active customer lists active followed sellers; seller summaries include `avatar_url` with an empty-string default
- `/api/feed/seller-packages/<seller_package_id>/follow/`
  - `POST`: authenticated active customer creates or reactivates a follow for a public active seller package
- `/api/feed/seller-packages/<seller_package_id>/unfollow/`
  - `POST`: authenticated active customer deactivates a seller package follow if present
- `/api/feed/seller-packages/following/`
  - `GET`: authenticated active customer lists active followed public seller packages
- `/api/feed/blocks/customer-seller/`
  - `GET`: authenticated active customer or seller lists active blocks involving the active profile
  - `POST`: authenticated active customer or seller blocks the opposite profile by `profile_id`
- `/api/feed/blocks/customer-seller/<profile_id>/`
  - `DELETE`: authenticated original block creator deactivates the active block for the opposite profile
- `/api/feed/people-you-may-know/`
  - `GET`: authenticated active customer gets public-safe active seller suggestions; seller suggestions include `avatar_url` with an empty-string default
- `/api/feed/auctions/`
  - `GET`: authenticated active customer gets followed-seller and followed-package active auctions first, then discovery auctions
- `/api/feed/market/`
  - `GET`: authenticated active customer gets followed-seller and followed-package market-listed products first, then discovery products

Current feed behavior:

- `SellerFollow.follower` points to `CustomerProfile`
- `SellerFollow.seller` points to `SellerProfile`
- `SellerFollow.is_active` is toggled for unfollow/refollow; follows are not hard-deleted by the API
- only one `SellerFollow` row can exist per follower/seller pair
- customers cannot follow a seller profile on the same user account
- customers cannot follow inactive, invalid, unactivated, or deleted sellers
- repeated follow returns the existing active row or reactivates an inactive row
- repeated unfollow is safe and leaves the row inactive
- following lists exclude inactive, invalid, unactivated, or deleted sellers
- `SellerPackageFollow.follower` points to `CustomerProfile`
- `SellerPackageFollow.seller_package` points to `SellerPackage`
- `SellerPackageFollow.is_active` is toggled for unfollow/refollow; follows are not hard-deleted by the API
- only one `SellerPackageFollow` row can exist per follower/seller-package pair
- customers cannot follow seller packages owned by a seller profile on the same user account
- customers cannot follow inactive, unactivated, private, expired, or seller-owner-inactive packages
- seller-package following lists exclude inactive, unactivated, private, expired, blocked, or seller-owner-inactive packages
- seller suggestions require an authenticated active customer profile
- seller suggestions return only `SellerProfile` rows and exclude followed sellers, same-user seller profiles, inactive, invalid, unactivated, and deleted sellers
- seller suggestions expose public-safe seller fields only and do not expose user, phone, email, balance, private, or admin fields
- seller suggestions default to `limit=10`, cap `limit` at `50`, and apply Django `Random()` only after all eligibility filters
- `SellerCustomerBlock.customer` points to `CustomerProfile`
- `SellerCustomerBlock.seller` points to `SellerProfile`
- `SellerCustomerBlock.blocked_by` points to the customer or seller profile that created the block
- only one block row can exist for a customer/seller pair; unblock/reblock toggles `is_active`
- only customer/seller pairs can be blocked; customer/customer, seller/seller, manager/admin, and same-user customer/seller profile blocks are rejected
- block creation deactivates any active `SellerFollow` and `SellerPackageFollow` between the customer and seller
- only the profile that created the block can unblock it
- active blocks prevent following, hide blocked sellers from following and discovery feed results, and hide blocked sellers from seller suggestions
- active blocks prevent new chat conversations, new chat messages in existing conversations, and new auction/market payment requests between the pair
- active blocks do not delete old follows, orders, payment requests, conversations, or messages; historical records remain intact
- feed endpoints require an authenticated active customer profile
- auction feed content is limited to visible, currently active `AuctionStatus.ACTIVE` auctions from active valid sellers
- market feed content is limited to available, listed, positive-count supply/accessory/feed/supplement assets from active valid sellers
- feed ordering is stable by `created` descending, then `id` descending
- feed responses put followed results before `source="discovery"` results
- followed-seller content uses `source="following"` and followed-package-only content uses `source="package_following"`
- discovery excludes content from followed sellers, followed packages, and IDs already returned in the same response
- if followed content is exhausted or absent, discovery fills the remaining limit
- feed `limit` defaults to `20`, is capped at `50`, and must be at least `1`
- feed cursors are URL-safe base64 JSON with separate `following` and `discovery` positions plus `following_exhausted`
- invalid cursor payloads return a clean validation error
- cursor feeds do not use `order_by("?")`, SQL random functions, seen-item tracking, personalization scores, or recommendation ranking

Current feed service implementation names:

- `SellerFollowService.follow()` creates, returns, or reactivates a seller follow
- `SellerFollowService.unfollow()` idempotently marks a seller follow inactive
- `SellerFollowService.following_queryset()` returns active followed sellers for the active customer
- `SellerPackageFollowService.follow()` creates, returns, or reactivates a seller package follow
- `SellerPackageFollowService.unfollow()` idempotently marks a seller package follow inactive
- `SellerPackageFollowService.following_queryset()` returns active followed seller packages for the active customer
- `BlockService.block_customer_seller()` creates or reactivates a customer/seller block and deactivates any existing seller or seller-package follow
- `BlockService.unblock_customer_seller()` deactivates an active block when called by the original blocker
- `BlockService.is_blocked_between()` checks active block state for customer/seller interaction gates
- `BlockService.get_blocked_seller_ids_for_customer()` returns seller ids hidden from customer feed and suggestion surfaces
- `BlockService.get_blocked_customer_ids_for_seller()` returns customer ids blocked with a seller
- `FeedService.get_people_you_may_know()` returns random active seller suggestions after customer-specific exclusions
- `FeedService.get_auction_feed()` builds the auction feed with followed seller/package content first and deterministic discovery fallback
- `FeedService.get_market_feed()` builds the market feed with followed seller/package content first and deterministic discovery fallback
- `FeedCursor` parses and encodes the two-source cursor state

### `apps.insights`

Current responsibilities:

- seller home-page summary metrics
- lightweight seller insight view events
- safe aggregation over existing auction, market, feed, chat, rating, payment, and package data

Important note:

Seller insights are MVP dashboard summaries over current marketplace data. This app is not a full reporting system, advanced charting module, recommendation engine, analytics export, admin analytics dashboard, deduplicated view tracker, device/IP tracker, notification system, frontend module, or Celery rollup pipeline.

Current insights API:

- `/api/insights/seller/home/`
  - `GET`: authenticated active seller gets their own home-page summary

Current insights behavior:

- seller insights require an authenticated active seller profile
- customer and manager active profiles are rejected for seller home insights
- `SellerInsightsService.get_home_summary()` owns aggregation logic
- the home summary returns `auction_summary`, `market_summary`, `engagement_summary`, `trust_summary`, and `package_summary`
- missing optional features return safe defaults instead of creating unrelated systems
- promoted auction and market-item counts currently return `0` because no promoted listing model exists
- package quota summary is derived from a currently active `SellerPackage.remaining_points` divided by package auction/product costs
- recent windows use the last 7 days with timezone-aware `timezone.now()`
- ending-soon auction count currently uses active seller auctions ending within the next 24 hours
- low-stock market product count currently uses market-listed available product assets with `count` from `1` through `3`
- `SellerInsightEvent` stores seller, optional actor profile, event type, object type, optional object id, and timestamps only
- event types are `seller_profile_view`, `auction_view`, and `market_item_view`
- object types are `seller_profile`, `auction`, and `market_item`
- insight events do not store IP address, device, user-agent, deduplication keys, or daily rollups
- event recording is best-effort through `SellerInsightEventService.record_event()`; failures are logged and return `None`
- current low-risk view-event hooks record auction detail views and product asset detail views only, not list endpoints

Current insights service implementation names:

- `SellerInsightsService.get_home_summary()` returns the seller home summary
- `SellerInsightEventService.record_event()` records a small view event without breaking caller flows

### `apps.pedigrees`

Current responsibilities:

- internal pedigree document upload records
- current image-box detection plus per-box OCR extraction for uploaded pedigree documents
- document retrieval and manual review of extracted values
- independent private `PedigreeBird` records registered by the active customer or seller profile (`registered_by`), with separate, nullable physical ownership (`owner`)
- synchronous external FastReport reports-list and PDF/image generation proxying
- `RegisteredPedigree` registry of FastReport pedigree ids (`pdf_id` / `shape`) with public/private visibility and `UserAccount` assignments; it is the PPW authorization source of truth for every user-facing endpoint that uses a pedigree id

Important note:

This app owns private pedigree-bird workflows alongside the OCR document workflow. `PedigreeBird` is independent from `apps.assets.models.Bird`; marketplace and auction flows must not depend on it. `PedigreeBird` father/mother links are the canonical current pedigree graph. PPW does not persist report templates, generated certificates, generated files, report jobs, provider request history, or provider response history; it stores only registered pedigree ids plus their visibility, and each bird's report `default_color`. Report generation is a synchronous proxy call to the external FastReport provider using current `PedigreeBird` data.

Current pedigree API:

- `/api/pedigrees/birds/`
  - `GET`: customer/seller lists only pedigree birds registered by the active profile, owned or not; optional `?owner_id=<profile_id>` only narrows that registration scope to physically owned birds; manager lists all with `pedigrees.birds.list`
  - `POST`: active customer or seller creates a private pedigree bird registered by the active profile; optional `owner` may be omitted/`null` (stored as `owner=NULL`, `ownership_status=unknown`) or the active profile itself (`owned`)
- `/api/pedigrees/birds/<id>/`
  - `GET` / `PATCH` / `DELETE`: registration-scoped (`registered_by`) private pedigree bird detail, update, and soft delete; managers require explicit bird permissions and may assign, reassign, or clear `owner` (any customer/seller profile) with `pedigrees.birds.update`
- `/api/pedigrees/birds/public/<public_id>/claim/`
  - `POST`: active customer/seller takes physical ownership of any live unowned pedigree bird by `public_id`, including one registered by another profile; sets only `owner` and `ownership_status=owned`, is idempotent for the current owner, returns `404` for missing birds and birds owned by someone else, and grants no pedigree visibility; managers are denied
- `/api/pedigrees/birds/public/<public_id>/release/`
  - `POST`: the current owner gives up ownership (`owner=NULL`, `owned` becomes `unknown`); anyone else gets `404`; managers are denied
- `/api/pedigrees/birds/import/`
  - `POST`: manager/superuser-only multipart XLSX import using exactly `file` and `user` (`UserAccount` id); reuses `pedigrees.birds.create` manager permission and returns aggregate import counts only
- `/api/pedigrees/registered/` and `/api/pedigrees/available/`
  - `GET`: customer/seller lists public registered pedigrees plus private ones assigned to their `UserAccount` (`id`, `pedigree_id`, `is_public`, `access_type`: `"public"` | `"custom"`); manager lists every registration with `pedigrees.registered.list`, including `user_ids`, `created`, and `modified`; supports `pedigree_id`, `is_public`, and `ordering` (`pedigree_id`, `created`) filters that only narrow the authorized queryset
  - `POST` (`/api/pedigrees/registered/` only): manager with `pedigrees.registered.create` registers `{"pedigree_id": <pdf_id>, "is_public": <bool, default true>, "user_ids": [<UserAccount id>, ...]}`
- `/api/pedigrees/registered/<id>/`
  - `GET`: customer/seller retrieves a visible registration by DB id (otherwise `404`); manager retrieves any with `pedigrees.registered.retrieve`
  - `PATCH`: manager with `pedigrees.registered.update` changes `pedigree_id`, `is_public`, and/or `user_ids`
  - `DELETE`: manager with `pedigrees.registered.delete` soft-deletes the registration
- `/api/pedigrees/available/<pedigree_id>/`
  - `GET`: customer/seller retrieves a visible registration directly by `pedigree_id` (FastReport shape/template id); returns `200` if public or assigned to current `UserAccount`, `404` otherwise (fail closed); manager retrieves any visible registration
- `/api/pedigrees/templates/`
  - `GET`: manager raw proxy to `{PEDIGREE_REPORT_BASE_URL}/api/reports/fastreport/reports`; returns upstream status/body/content type without local storage or reshaping
  - `GET`: customer/seller receives `503` without any provider call (fails closed) because server-side filtering of the provider list is blocked until the real provider response shape is verified; clients use `/api/pedigrees/available/` or `/api/pedigrees/registered/` for the pedigree ids they may use
- `/api/pedigrees/templates/<shape>/schema/`
  - `GET`: authenticated raw proxy to `{PEDIGREE_REPORT_BASE_URL}/api/reports/fastreport/schema/{shape}?generation=<generation>`; validates the integer generation query, then returns `404` for customer/seller unless `shape` is a visible registered pedigree, before any provider call
- `/api/pedigrees/templates/<shape>/style-parameters/`
  - `GET`: authenticated raw proxy to `{PEDIGREE_REPORT_BASE_URL}/api/reports/fastreport/style-parameters?pedigreeType=<shape>&generation=<generation>`; same registry gate as schema; returns provider-owned style metadata without local persistence or reshaping
- `/api/pedigrees/generate/`
  - `POST`: returns `404` for customer/seller unless `pdf_id` (or alias `pedigree_id`) is a visible registered pedigree, then synchronously merges current local pedigree bird parameters into the frontend-supplied report object, verifies the active Loft Manager subscription allows the selected `pdf_id`, calls `{PEDIGREE_REPORT_BASE_URL}/api/reports/fastreport/pdf/{pdf_id}`, and returns the upstream status/body/content type without storing the report; an explicit `color` is saved as the `default_color` of every printed bird of the subject's registrant only after a 2xx provider response
- `/api/pedigrees/provider-test/`
  - `POST`: manager-only dashboard diagnostic that calls provider `/health`, renders one deterministic in-memory Shape 04 Generation 6 synthetic PDF through `{PEDIGREE_REPORT_BASE_URL}/api/reports/fastreport/pdf/4`, validates the returned PDF, discards the bytes, and returns compact JSON health/generation timing and status data
- `/api/pedigrees/documents/`
  - `POST`: authenticated customer or seller uploads a pedigree document file for OCR extraction
- `/api/pedigrees/documents/<id>/`
  - `GET`: owner retrieves one pedigree document; managers retrieve any document
- `/api/pedigrees/documents/<id>/review/`
  - `PATCH`: owner submits reviewed pedigree bird nodes using F/M paths; managers can review any document

Current pedigree behavior:

- `PedigreeBird` keeps three independent concepts:
  - `registered_by` (required, server-assigned, read-only in API input, never changed by ownership operations) is the registration authority: it alone controls visibility/queryset scope, ring reuse/matching, normalized ring uniqueness, and which birds may be linked as father/mother
  - `owner` (nullable, `SET_NULL`) is physical/business ownership only and is never used for visibility, matching, or parent links
  - `ownership_status` (`owned`, `sold`, `gifted`, `unknown`; default `unknown`) is the ownership state; `owner=NULL` with `ownership_status=unknown` is a first-class valid state, and `owned` without an owner is rejected
- parentage never implies ownership: becoming a father/mother/ancestor never changes `owner` or `ownership_status`
- a registered pedigree's subject is registered and owned by the registering profile (`owned`); every ancestor it creates (parents, grandparents, great-grandparents, and deeper) is registered by that profile with `owner=NULL` and `ownership_status=unknown`
- customers/sellers may set `owner` only to their own active profile or `NULL` and may never overwrite or clear another profile's ownership; managers may assign any customer/seller profile; owner changes keep `owned`/`unknown` in step but preserve explicit `sold`/`gifted`
- claiming a bird grants no pedigree visibility; a claimed bird stays visible only to its `registered_by` profile
- historical rows were backfilled with `registered_by = owner` and keep their owners: historical subjects and ancestors cannot be told apart, so no ownership is inferred or cleared
- `PedigreeBird.public_id` is UUID-backed and read-only
- pedigree bird ring numbers are normalized by trimming, uppercasing, and collapsing whitespace into `normalized_ring_number`
- one live pedigree bird per `registered_by` profile can use a normalized ring number; soft-deleted birds do not block reuse and ring numbers are not globally unique
- `PedigreeBird` parent links are self-FKs for father and mother and require `parent.registered_by == child.registered_by`; parent choices are scoped so another registrant's bird is indistinguishable from a missing one
- pedigree bird validation rejects future birthday/record dates, self parents, same father/mother, parents registered by another profile, wrong known parent sex, parent birthdays not before child birthdays, deleted parents, and direct or indirect cycles
- the manager XLSX adapter imports only the verified legacy ring tree, root sex (`ذكر` / `أنثى` / `مجهول`), and canonical description text into `PedigreeBird`; it ignores all other legacy columns, sentinels (`0`, `102`, `ffffffff`, blanks), and `Estok_Date`, creates no marketplace `Bird` records, and has no commerce side effects
- the XLSX adapter uses the selected `UserAccount` without consulting or switching `active_profile`: the target must have exactly one eligible live customer/seller profile, which becomes `registered_by` for every imported bird; none or more than one is rejected rather than choosing a profile implicitly
- XLSX rows are subjects: a ring that is the root (`Estok_No`) of any row is owned by the target profile, and every other imported ring is an ancestor with `owner=NULL` / `unknown`
- XLSX import is fully transactional and idempotently reuses normalized rings registered by the target profile only; a reused unowned subject is claimed, while reused ancestors and existing non-null owners are never changed; conflicting supplied/existing non-empty canonical scalar or parent data is rejected, while missing known parent links may be filled through `PedigreeBirdValidator`
- `PEDIGREE_REPORT_BASE_URL` is required for report proxy calls and must come from environment configuration; Python code must not default to the provider IP
- `PEDIGREE_REPORT_API_KEY` is required for FastReport provider calls and must only be sent backend-to-provider as `X-Api-Key`; do not expose it through PPW APIs, serializers, logs, or frontend configuration
- `PEDIGREE_REPORT_TIMEOUT` is the single report-provider timeout setting
- `/api/pedigrees/templates/` does not read, parse, filter, cache, or save provider report definitions; for customers/sellers it currently fails closed with `503` instead of returning the unfiltered list
- `RegisteredPedigree` (`BaseModel`, soft delete) stores `pedigree_id` (the FastReport `pdf_id` / `shape`, positive integer, unique among live rows) and `is_public` (default `true`, preserving the previous globally usable behavior for ids managers register)
- `RegisteredPedigreeUser` is the only bridge: unique `(pedigree, user)` rows meaning "this `UserAccount` may see this private pedigree"; no roles, expiry, profile, or metadata
- registered pedigree visibility is `Q(is_public=True) | Q(assignments__user=request.user)` (with `distinct()`), applied in `get_queryset()` before filters, ordering, and pagination; unauthorized private rows never enter the queryset, so detail lookups return `404`
- visibility belongs to `UserAccount`, not `Profile`: switching Customer/Seller mode never changes it
- for customer/seller requests, unregistered ids and private ids not assigned to the user return `404` on `/generate/`, `/templates/<shape>/schema/`, and `/templates/<shape>/style-parameters/` before any provider call; knowing a `pdf_id` never bypasses PPW authorization
- managers and superusers are not limited by user visibility on provider endpoints and see every registered pedigree
- manager `user_ids` is the full desired assignment set: omitted on `PATCH` keeps assignments, `[]` clears them, a list replaces them inside one transaction; duplicate ids collapse and unknown `UserAccount` ids are rejected
- switching a pedigree between private and public never deletes assignments; public pedigrees ignore them and they apply again when made private
- a private pedigree with no assignments is valid and visible only to managers
- there is no generic ACL, separate bridge CRUD endpoint, profile-based entitlement, share token, expiry, or access-request workflow
- registering a pedigree id does not check the provider; existence validation against FastReport is not currently available
- Loft subscription checkout `template_ids` are not checked against the registry; generation still requires both a visible registration and subscription entitlement
- `/api/pedigrees/templates/<shape>/schema/` and `/api/pedigrees/templates/<shape>/style-parameters/` validate `generation` as an integer between 2 and `PEDIGREE_MAX_GENERATION_DEPTH`; do not hardcode report availability matrices because provider reports metadata is authoritative
- `/api/pedigrees/generate/` accepts `subject_bird_id`, `pdf_id`, `generation_depth`, frontend `report`, optional `owner`, and optional `customization`
- report generation validates registry visibility of `pdf_id`, local subject-bird access, minimal request shape, and active subscription template entitlement; it does not validate `pdf_id`, `report.generation`, provider parameter names, file names, or report metadata against the external reports list
- generation depth counts total pedigree levels with the subject as depth `1`; depth `2` includes only direct parents and depth is capped by the platform pedigree maximum
- `PedigreeReportMapper` deep-copies the frontend report object, preserves unknown report fields and unknown parameters, then overwrites only PPW-owned bird, owner, customization, and print-date parameters
- `PedigreeBird.colour` is the bird's biological/plumage colour; `PedigreeBird.default_color` (nullable `#RRGGBB`) is unrelated report styling: the last explicit report colour of a successful generation that printed that bird
- report colour is per bird box: explicit request `color` (`#RRGGBB`, normalized to upper case) for every box, otherwise that box bird's own stored `default_color`, otherwise no colour (there is no backend fallback colour); a bird's stored colour is read only when it is registered by the subject's registrant (the requesting profile for customers/sellers); omitted and `null` behave the same, and invalid values return `400` before any provider call
- a box's resolved colour fills `{Field}_BackColor` for each of that bird box's fields PPW writes (subject, parents, and mapped ancestors: ring, name, and description) only when the frontend did not send that per-field style or sent `null` or a blank string; explicit non-blank frontend per-field styles always win, other styles (`TextColor`, `BoxBackColor`, `BorderColor`, `BoxShape`) and non-bird fields are untouched, and a box with no resolved colour keeps whatever the frontend sent (the FastReport template default applies)
- only an explicit `color` is persisted, on every printed bird (within `generation_depth`) registered by the subject's registrant, and only after every generation gate (registered-pedigree visibility, `registered_by` subject scope or manager `pedigrees.reports.generate`, subscription entitlement) and a 2xx provider response, as one unlocked last-write-wins update; a different stored colour is overwritten and a matching one is left alone; failed or errored generations, null colours, and stored defaults are never written, and `default_color` is read-only on the bird CRUD API and untouched by OCR/XLSX
- `PedigreeReportClient` performs raw `GET /api/reports/fastreport/reports`, metadata `GET`, and `POST /api/reports/fastreport/pdf/{pdf_id}` calls with `X-Api-Key`, no MIME/file-signature validation, no generated-file storage, no Celery retry, and no response wrapping
- provider diagnostics are administrative infrastructure checks, not customer generation: they must not use `PedigreeBird` rows, profile pedigree data, documents, subscription entitlement, template allowance, AI quota, file storage, Celery, or database persistence
- provider diagnostics use deterministic synthetic owner/bird/style values only; do not include real user data, API keys, full provider payloads, or PDF bytes in responses or logs
- provider HTTP responses are passed through with upstream status, body, content type, content disposition, and safe cache headers; connection failures return a small `502` JSON response and timeouts return a small `504` JSON response
- safe report logs may include operation, subject bird id, pdf id, generation depth, upstream status, duration, and exception type only; do not log full payloads because they may include names, phone, WhatsApp, email, bird data, and image URLs
- `PedigreeDocument` inherits the standard profile-owned model shape and also stores `uploaded_by` as the user account
- `PedigreeDocument.bird` is nullable and can link to an existing `Bird` when supplied
- non-manager uploads can link only to birds owned by the active profile; managers can link any bird
- document statuses are `uploaded`, `processed`, `reviewed`, and `failed`
- upload creates a document with status `uploaded`, detects image boxes, then runs OCR for each detected box
- box-detection or OCR orchestration exceptions mark the document `failed`, store `processing_error`, and return a clear error response
- no detected boxes marks the document `failed` with a clear error response
- successful processing stores `extracted_boxes` with normalized box coordinates, OCR lines, and per-box OCR errors, then marks the document `processed`
- review accepts a `birds` list with `path` values such as `""`, `F`, `M`, `FF`, and `FM`; paths may contain only `F` and `M`, require existing prefixes, and are limited to six total levels including the subject
- review creates or reuses one live `PedigreeBird` per normalized ring number registered by the document owner, never reuses another profile's registered bird, updates only explicitly supplied fields, and treats explicit blank text as an intentional value
- review registers every node with `registered_by = document.owner`; a new subject (path `""`) gets `owner = document.owner` / `owned`, while new ancestors get `owner=NULL` / `unknown` (the subject's owner is never propagated); a reused unowned subject is claimed, reused ancestors and existing non-null owners are never changed, and an explicit `ownership_status=owned` on an owner-less node is rejected
- repeated normalized rings may appear at more than one path for inbreeding; conflicting scalar values or conflicting derived parent assignments are rejected
- review derives father/mother links exclusively from paths, preserves missing existing parent links when no child path is supplied, stores subject ring/description on the document, preserves OCR boxes/raw data, marks the document `reviewed`, and sets `reviewed_at`

Current pedigree service implementation names:

- `PedigreeBirdValidator` owns database-dependent pedigree bird validation, `registered_by`-scoped ring uniqueness and parent rules, the owned-without-owner check, and cycle detection
- `PedigreeBirdOwnershipService` owns physical ownership only: `registration_ownership()` (subject owned / ancestor `NULL` + `unknown` for OCR and XLSX), `validate_owner_change()`, `status_for_owner()`, and locked `claim()` / `release()` by `public_id`; it never changes `registered_by`
- `PedigreeReviewedBirdService.save_reviewed_pedigree()` transactionally saves reviewed OCR nodes into the canonical `PedigreeBird` graph
- `PedigreeTreeService` builds in-memory generation data and F/M path lookups from current `PedigreeBird` relations
- `PedigreeReportMapper` maps generation data into the irregular external report payload parameter names while preserving unrelated frontend report data
- `PedigreeReportClient` owns raw external reports-list and PDF-generation HTTP calls
- `PedigreeReportGenerationService` loads the accessible subject bird, builds the provider payload with per-box colours for synchronous generation, and returns the printed bird ids of the subject's registrant; `remember_report_color()` saves an explicit colour on those birds after provider success within the same generation scope
- `RegisteredPedigreeService.visible_queryset()` returns registered pedigrees visible to a user (all for managers) and `require_visible()` raises `404` for customer/seller pedigree ids outside it
- `PedigreeImageBoxDetectionService` detects OCR boxes in uploaded pedigree documents
- `PedigreeBoxOCRService` extracts text lines for each detected document box
- `PedigreeDocumentService.upload_and_process()` creates the document and stores OCR/extraction success or failure state
- `PedigreeDocumentService.review_document()` delegates structured review saves and document review metadata updates

### `apps.promotions`

Current responsibilities:

- admin-created immutable `DiscountOffer` records for marketplace order-item discounts
- admin-created immutable `CashbackOffer` records for automatic marketplace cashback earning and redemption
- customer `cashback_balance` storage on `CustomerProfile`
- immutable `CashbackTransaction` ledger records for every cashback balance movement
- admin cashback balance adjustments with audit trail
- customer/admin promotion and cashback APIs
- admin-managed scheduled `PromotionCampaign` records (`MONTHLY`, `SEASONAL`, `FLASH`) with timezone-aware half-open `[starts_at, ends_at)` windows, derived `effective_status` (`DISABLED`, `SCHEDULED`, `ACTIVE`, `ENDED`), canonical `PromotionCampaignService.active_campaigns()` lookup (BE-PRO-01), service-level percentage discount effects (`PromotionCampaignEffect`) with non-stacking highest-discount resolution via `PromotionPricingService.resolve_service_discount()` integrated into Seller Package requests and Loft Subscription pricing/checkout (BE-PRO-02), and the account-level onboarding 20% first auction-highlight discount entitlement, campaign comparison quote, and trusted atomic consumption service (`OnboardingHighlightBenefitService`, BE-PRO-03; actual paid highlight purchase wiring is deferred pending a canonical auction highlight domain)

Important note:

This app is not a payment gateway, withdrawal wallet, coupon-code system, cashback expiration engine, seller campaign tool, auction cashback module, or admin approval workflow. Cashback is an internal customer balance only.

Current promotions API:

- `/api/promotions/active/`
  - `GET`: authenticated list of currently active (`is_enabled=True`, `starts_at <= now < ends_at`) scheduled promotion campaigns, with optional `?campaign_type=MONTHLY|SEASONAL|FLASH`
- `/api/promotions/admin/campaigns/`
  - `GET`: manager with `promotions.campaigns.list` lists scheduled promotion campaigns (filterable by `campaign_type`, `is_enabled`, `effective_status`)
  - `POST`: manager with `promotions.campaigns.create` creates a scheduled promotion campaign
- `/api/promotions/admin/campaigns/<id>/`
  - `GET`: manager with `promotions.campaigns.retrieve` retrieves one scheduled promotion campaign
  - `PATCH`: manager with `promotions.campaigns.update` updates a scheduled promotion campaign (`campaign_type`, `starts_at`, and `ends_at` are editable only before `starts_at`)
- `/api/promotions/admin/campaigns/<id>/enable/`
  - `POST`: manager with `promotions.campaigns.enable` idempotently enables a promotion campaign
- `/api/promotions/admin/campaigns/<id>/disable/`
  - `POST`: manager with `promotions.campaigns.disable` idempotently disables a promotion campaign
- `/api/promotions/admin/campaigns/<campaign_id>/effects/`
  - `GET`: manager with `promotions.campaign_effects.list` lists service-level discount effects for a campaign
  - `POST`: manager with `promotions.campaign_effects.create` creates a `PERCENT_DISCOUNT` effect (`SELLER_PACKAGE`, `AUCTION_HIGHLIGHT`, `LOFT_SUBSCRIPTION`) before `campaign.starts_at`
- `/api/promotions/admin/campaigns/<campaign_id>/effects/<id>/`
  - `GET`: manager with `promotions.campaign_effects.retrieve` retrieves one campaign effect
  - `PATCH`: manager with `promotions.campaign_effects.update` updates a campaign effect before `campaign.starts_at`
  - `DELETE`: manager with `promotions.campaign_effects.delete` soft-deletes a campaign effect before `campaign.starts_at`
- `/api/promotions/discount-offers/current/`
  - `GET`: customer-visible active discount offers
- `/api/promotions/cashback-offers/current/`
  - `GET`: customer-visible active cashback offers
- `/api/promotions/cashback/my/`
  - `GET`: customer lists their own cashback transactions
- `/api/promotions/my-grants/`
  - `GET`: authenticated user lists their own active, non-expired user promotion grants
- `/api/promotions/onboarding/highlight-benefit/`
  - `GET`: authenticated user reads their account-level onboarding 20% first auction-highlight discount status (`has_benefit`, `is_available`, `is_consumed`, `discount_percent`, `target_type`, `consumed_at`, `source_id`) without mutating or backfilling state
- `/api/promotions/cashback/balance/`
  - `GET`: customer retrieves their current cashback balance
  - `GET ?customer=<id>`: manager with `cashback.balance.retrieve` retrieves one customer's cashback balance; manager without `customer` and non-manager use of `customer` return 403
- `/api/promotions/admin/discount-offers/`
  - `GET`: manager lists discount offers
  - `POST`: manager creates a discount offer
- `/api/promotions/admin/discount-offers/<id>/activate/`
  - `POST`: manager activates a discount offer
- `/api/promotions/admin/discount-offers/<id>/deactivate/`
  - `POST`: manager deactivates a discount offer
- `/api/promotions/admin/cashback-offers/`
  - `GET`: manager lists cashback offers
  - `POST`: manager creates a cashback offer
- `/api/promotions/admin/cashback-offers/<id>/activate/`
  - `POST`: manager activates a cashback offer
- `/api/promotions/admin/cashback-offers/<id>/deactivate/`
  - `POST`: manager deactivates a cashback offer
- `/api/promotions/admin/cashback-transactions/`
  - `GET`: manager lists all cashback transactions
- `/api/promotions/admin/cashback-adjustments/`
  - `POST`: manager creates a positive or negative cashback balance adjustment

Current promotions behavior:

- offer value types are `percentage` and `fixed_amount`
- offer trigger types are `global`, `lucky_wheel`, `onboarding`, `referral`, `season`, and `manager_grant`
- normal automatic checkout discount and cashback selection considers only active `global` offers
- non-global offers must be tied to a user through `UserPromotionGrant` before they are treated as a user-owned prize/grant
- `UserPromotionGrant` records belong to a `UserAccount`, optionally keep the causing `Profile`, point to exactly one `DiscountOffer` or `CashbackOffer`, and use a unique idempotency key
- user promotion grants exposed through `/api/promotions/my-grants/` are scoped to the current user/profile and exclude expired, redeemed, and cancelled grants
- offer target types are `all`, `category`, and `asset`
- offer values must be greater than zero
- percentage offer values cannot exceed `100`
- target validation requires an asset for `asset`, a category for `category`, and neither for `all`
- discount and cashback offers are immutable after creation except `is_active`
- managers can activate or deactivate offers; offer update endpoints are intentionally not exposed
- discount selection considers only active eligible offers and chooses by calculated discount, target specificity, priority, and newest creation time
- cashback selection considers only active eligible offers and chooses by calculated earned cashback, target specificity, priority, and newest creation time
- discounts apply when an `OrderItem` is created and reduce `OrderItem.total_price` immediately
- cashback redemption happens only when the seller approves an `OrderItem`
- cashback earning happens only after a seller approves the related `MarketPaymentRequest` and the market sale completes
- cashback earning and redemption use idempotency keys based on `order_item.id`
- admin adjustments use a system `CashbackOffer` titled `Manual Admin Adjustment`
- every cashback balance change must create exactly one `CashbackTransaction`
- cashback balance mutations use `transaction.atomic()`, `select_for_update()` on `CustomerProfile` (and `OrderItem` where applicable), and post-lock idempotency re-checks before mutating balances
- `CashbackService.refund_redeemed_for_order_item()` exists for future approved-item cancellation flows; current seller rejection and seller timeout paths do not call it because they only affect pending seller items
- `CashbackService.buy_points_with_cashback()` atomically locks `CustomerProfile`, validates idempotent replay payload consistency (`idempotency_key` conflict rejection) and minimum 1-PP conversion (`cashback_amount >= 0.05` at 20 PP per 1 cashback unit), deducts `CustomerProfile.cashback_balance`, writes a `points_purchase` `CashbackTransaction`, and credits `PointWallet` (`UserAccount`) via `PointService.earn_points(source_type=CASHBACK_POINTS_PURCHASE)` without creating `PrizeAward` rows
- `BE-CB-01` product alignment status is `PARTIAL / BLOCKED`: current purchase cashback is earned as monetary units on `CustomerProfile.cashback_balance` from admin-configured `GLOBAL` `CashbackOffer` rows (which accept `0 < value <= 100%` or `fixed_amount`, not a canonical `1%–5%` rule) only after `MarketPaymentRequest` approval, can be manually converted to `UserAccount` PP Coins via `POST /api/loyalty/points/buy-with-cashback/` (at hardcoded `20 PP / 1 unit`), and cannot be withdrawn as cash; automatic `1%–5%` `CompletedDeal` PP conversion, canonical currency-to-PP conversion rates, and auction spending of cashback/PP Coins remain blocked pending product decisions

Current promotions service implementation names:

- `DiscountOfferService.choose_best_offer()` selects the best active discount offer for an asset and gross total
- `DiscountOfferService.calculate_discount()` calculates the immediate discount amount
- `CashbackOfferService.choose_best_offer()` selects the best active cashback offer for an asset and payable amount
- `CashbackOfferService.calculate_cashback_earned()` calculates cashback to credit after sale completion
- `CashbackOfferService.calculate_redemption_amount()` calculates automatic redemption from current customer balance
- `CashbackService.redeem_for_order_item()` deducts redeemed cashback and writes a redeemed transaction
- `CashbackService.earn_for_completed_order_item()` credits earned cashback and writes an earned transaction
- `CashbackService.refund_redeemed_for_order_item()` refunds redeemed cashback idempotently when applicable
- `CashbackService.admin_adjust()` applies manager-only manual balance adjustments
- `UserPromotionGrantService.grant_discount()` and `grant_cashback()` create idempotent user-owned promotion grants
- `PromotionCampaignService.create()`, `update()`, `enable()`, `disable()`, `effective_status()`, and `active_campaigns()` manage scheduled promotion campaigns (`MONTHLY`, `SEASONAL`, `FLASH`) and active-window queries (BE-PRO-01)
- `PromotionCampaignEffectService.list_for_campaign()`, `create()`, `update()`, and `delete()` manage service-level `PromotionCampaignEffect` records and freeze effect mutations once `now >= campaign.starts_at` (BE-PRO-02)
- `PromotionPricingService.resolve_service_discount()` (aliased as `PromotionEffectService`) resolves non-stacking highest-discount `PERCENT_DISCOUNT` effects across active campaigns (`-discount_percent`, `campaign.starts_at`, `campaign.id`, `effect.id`) for `SELLER_PACKAGE`, `LOFT_SUBSCRIPTION`, and `AUCTION_HIGHLIGHT` targets (BE-PRO-02)
- `OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding()`, `ensure_for_onboarding()`, `has_prior_onboarding_profile_history()`, `get_benefit()`, `is_available()`, `get_benefit_status()`, `quote()`, and `consume_if_selected()` (`commit_highlight_benefit()`) manage the one-time `UserAccount`-level 20% first auction-highlight discount backed by `UserPromotionGrant` (`idempotency_key=promotion:onboarding_highlight:user:{user_id}`), grant it automatically only on the account's first-ever `Customer`/`Seller` profile in `Profile.all_objects` (so later mode creation or soft-deleted profile history never backfills historical accounts), compare it against active `AUCTION_HIGHLIGHT` campaign discounts without stacking (onboarding wins equal-value 20% ties; higher campaigns preserve the onboarding benefit), and atomically consume it on trusted commit (BE-PRO-03 / BE-PRO-03A)

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

Auction closing resolves winners and marks items sold or unsold. It does not auto-create payment requests. A winning customer manually creates an `AuctionPaymentRequest`, and seller approval in `apps.payments` transfers bird ownership.

Public auction chat/comments are still out of scope in the current backend. There is no `Auction.chat_enabled` field, no `AuctionComment` model, and no auction comment endpoint.

Current auction API:

- `/api/auctions/`
  - `GET`: authenticated list of visible auctions for allowed roles (supports optional `?room=<id>` / `?room_id=<id>` filtering)
  - `POST`: seller creates an auction with nested items (plus optional `room` / `room_id`, `shipping_method`, `min_shipping_days`, `max_shipping_days`, or convenience `shipping_days`)
- `/api/auctions/<id>/`
  - `GET`: retrieve auction detail (`allow_scoped_read_access=True` for authenticated readers)
  - `PATCH` / `PUT`: owner updates only `title`, `description`, `tags`, optional owner-scoped `room` / `room_id`, and optional per-auction shipping fields (`shipping_method`, `min_shipping_days`, `max_shipping_days`, `shipping_days`) while editable
  - `DELETE`: owner or manager soft-deletes through the default `ModelViewSet` destroy path
- `/api/auctions/my_auctions/`
  - `GET`: seller lists own auctions, optionally filtered by `status` and/or `room`
- `/api/auctions/active/`
  - `GET`: list visible auctions whose time window is currently active (supports optional `?room=<id>` filtering)
- `/api/auctions/ending_soon/`
  - `GET`: list visible auctions ending within the requested `minutes`
- `/api/auctions/<id>/cancel/`
  - `POST`: owner or manager cancels an auction through `AuctionService.cancel_auction`
- `/api/auctions/<id>/close/`
  - `POST`: admin/staff manually closes an auction and resolves item winners
- `/api/auctions/<id>/seller-payment-details/`
  - `GET`: authenticated winning buyer account retrieves private seller payment coordination details (`seller_id`, `auction_id`, `bank_account`, `wallet`, `transfer_phone`) after the auction has ended and a winning item (`status=sold`, `winner__user=request.user`) is settled
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
- auction bid visibility values are `public` and `sealed`; `AuctionType` remains only the item-structure classifier and `sealed` is not an auction type
- auction statuses are `active`, `ended`, `cancelled`, and `closed`
- auction item statuses are `active`, `sold`, `unsold`, `withdrawn`, and `cancelled`
- `Auction.seller_package` is a nullable FK to `SellerPackage`; existing rows and manager-created rows can be null
- `Auction.room` is a nullable FK to `users.SellerRoom` (`on_delete=SET_NULL`); legacy auctions keep `room=NULL`, and when `room` is supplied `AuctionCreateSerializer`, `AuctionUpdateSerializer`, `AuctionBusinessValidator`, and `AuctionService` enforce `auction.room.seller_id == auction.owner_id` (BE-ROOM-01)
- `Auction` stores optional per-auction shipping settings (`shipping_method`, `min_shipping_days`, `max_shipping_days`) using `AuctionShippingMethod` (`PERSONAL_PICKUP`, `SHIPPING_COMPANY`, `INTERNATIONAL_SHIPPING`, `HAND_DELIVERY`); fixed durations (e.g. `7` days) are represented as `min_shipping_days=7, max_shipping_days=7`, ranges (e.g. `3–5` days) use `min_shipping_days=3, max_shipping_days=5`, and DB `CheckConstraint`s / model `clean()` / service / serializer validation enforce `min_shipping_days >= 1`, `max_shipping_days >= 1`, and `min_shipping_days <= max_shipping_days`; automated late-shipping warning/violation enforcement is `BLOCKED` pending product specification of shipment start/dispatch timestamps and violation records (BE-SHIP-01)
- auction creation requires future `start_time`, later `end_time`, and nested item payloads
- `AuctionCreateSerializer` owns auction creation field-shape checks such as future/later times, `bid_visibility`, optional per-item `reserve_price`, auto-extend minutes, deposit payment deadline, buy-now price, per-auction shipping fields, and required `paired_bird_id` for pair-style auctions
- `AuctionItem.reserve_price` is optional; when supplied it must be greater than or equal to `starting_price`
- `AuctionBusinessValidator` enforces item counts by auction type, seller bird ownership for seller-created auctions, seller room ownership, per-auction shipping invariants, pair gender rules, duplicate bird rejection, and overlapping active-auction bird rejection
- manager-created auctions bypass seller bird ownership validation because managers are using the administrative auction creation path
- `AuctionService.create_auction()` writes a `created` event
- seller auction creation requires `seller_package_id`, pre-validates that selected package, then creates the auction/items and consumes `Package.auction_cost` in the same DB transaction
- seller-created auctions are linked to the consumed `SellerPackage`
- manager-created auctions bypass seller package consumption and keep `seller_package=NULL`
- auction update, cancellation, close, bidding, and buy-now do not consume package points
- auction list/detail serializers expose FE-safe read-only fields including `room`, `room_name`, `shipping_method`, `shipping_method_display`, `min_shipping_days`, `max_shipping_days`, `is_active`, `is_ended`, `bid_visibility`, `buy_now_price`, `buy_now_enabled`, `min_bid_increment`, `current_price`, `thumbnail_url`, and `is_owner`; `chat_enabled` is not exposed because no such model field exists
- `AuctionEventSerializer` exposes only event audit metadata (`id`, `auction`, `event_type`, `event_type_display`, `item`, `bid`, `performer`, `metadata`, `created`, `modified`) and never exposes `performer.user.phone_number`
- `AuctionService.get_seller_payment_details_for_winner()` enforces that only the authenticated `UserAccount` owning a settled winning `AuctionItem` (`status=sold`, `winner__user=request.user`) on an ended, non-cancelled, non-deleted `Auction` with an active/valid `SellerProfile` may read the seller's `bank_account`, `wallet`, and `transfer_phone` (with `Cache-Control: private, no-store`), without requiring a `PaymentRequest` or `CompletedDeal` precondition (BE-PAY-PRIV-01)
- sealed-auction buyer-facing serializers hide active competitive data: bid lists, current highest bidder, reserve price, and current price; owner/manager views can see full bid and reserve details
- auction item detail exposes `winner_payment_request_id` through the existing auction payment-request relation when present
- `BidBusinessValidator` requires a customer profile, an active time window, an active item, and starting-price compliance; public auctions also require current-highest plus minimum increment compliance, while sealed auctions do not compare against or reveal the current highest bid
- bid placement revalidates under an `AuctionItem` row lock before creating the bid
- bid placement creates a best-effort in-app notification for the auction owner with `auction_id`, `auction_item_id`, and `bid_id` metadata
- public bid placement updates `AuctionItem.current_price`; sealed bid placement keeps `current_price` at the starting price until settlement, optionally auto-extends, and writes a `bid_placed` event
- sealed auction bid list/retrieve access is restricted to a bidder's own bids, the auction owner, and managers; public auctions keep the existing bid read behavior
- auto-extend uses the module constant `AUCTION_AUTO_EXTEND_MINUTES = 10` as the trigger window and extends by `auction.auto_extend_minutes`
- `AuctionService.update_auction()` allows updates only when status is `active`, the start time is still in the future, and no bids exist
- scheduled closing is handled by Celery Beat through `auctions.tasks.close_expired_auctions`
- `AuctionService.close_expired_auctions()` selects active auctions with `end_time <= now`
- `AuctionService.close_auction()` is idempotent for already closed auctions
- closing an auction resolves each item by `order_by("-amount", "created", "id")`, so highest amount wins, ties go to the earliest server-created bid, and exact timestamp ties go to lower id
- closing an auction marks each item `sold` with winner data when a qualifying bid exists, otherwise `unsold`, applies reserve-price failures as unsold with no winner, sets the auction to `closed`, and writes an `auction_ended` event
- closing an auction creates best-effort in-app `auction_won`, `auction_lost`, or `auction_reserve_not_met` notifications as applicable; loser and reserve notifications do not expose winning/highest amounts
- closing an auction does not create `PaymentRequest` records automatically
- sold seller-owned auction items can be finalized through buyer-created payment requests
- buy-now creates a bid at `auction.buy_now_price`, marks the item sold, sets winner fields, updates current price, writes a `buy_now_triggered` event, and creates best-effort `auction_won` and buy-now success notifications for the buyer

### `apps.ratings`

Current responsibilities:

- asset comments and seller comments
- customer ratings for assets and sellers through separate concrete models
- rating eligibility validation against completed order items
- cached rating aggregate refresh for assets and seller profiles
- deal-scoped `DealRating`: buyer and seller of one `CompletedDeal` rate each other once, separate from the public asset/seller ratings

Current ratings/comment API:

- `/api/asset-comments/`
  - `GET`: authenticated paginated asset comment list; supports `asset_id`, `owner_id`, and ordering filters
  - `POST`: authenticated active-profile asset comment creation with nested `asset`
- `/api/asset-comments/<id>/`
  - `GET`: manager detail retrieval with `comments.retrieve`
  - `DELETE`: customer owner self-delete or manager moderation delete with `comments.delete`
- `/api/seller-comments/`
  - `GET`: authenticated paginated seller comment list; supports `seller_id`, `owner_id`, and ordering filters
  - `POST`: authenticated active-profile seller comment creation with nested `seller`
- `/api/seller-comments/<id>/`
  - `GET`: manager detail retrieval with `comments.retrieve`
  - `DELETE`: customer owner self-delete or manager moderation delete with `comments.delete`
- `/api/asset-ratings/`
  - `GET`: authenticated paginated asset rating list; supports `asset_id`, `stars`, and ordering filters
  - `POST`: authenticated customer-profile asset rating creation with nested `asset` and optional nested `comment`
- `/api/asset-ratings/<id>/`
  - `GET`: manager detail retrieval with `ratings.retrieve`
  - `DELETE`: manager moderation delete with `ratings.delete`
- `/api/seller-ratings/`
  - `GET`: authenticated paginated seller rating list; supports `seller_id`, `stars`, and ordering filters
  - `POST`: authenticated customer-profile seller rating creation with nested `seller` and optional nested `comment`
- `/api/seller-ratings/<id>/`
  - `GET`: manager detail retrieval with `ratings.retrieve`
  - `DELETE`: manager moderation delete with `ratings.delete`
- `/api/deal-ratings/`
  - `POST`: the active buyer or seller profile of a `CompletedDeal` rates the other party with `deal_id`, `stars` (1-5), and optional `comment`; the direction and rated profile are derived, never submitted
- `/api/deal-ratings/deals/<deal_id>/`
  - `GET`: deal participant reads `role`, `has_rated`, `counterparty_has_rated`, `is_mutual`, `my_rating`, and `counterparty_rating` (hidden until both have rated); non-participants get 404

Current deal rating behavior:

- `DealRating` stores deal, direction (`buyer_to_seller` / `seller_to_buyer`), rater and rated `Profile`, `stars`, and `comment`
- unique `(deal, rater_profile)` and `(deal, direction)` over all rows, including soft-deleted ones, so a rating cannot be recreated; rater cannot equal rated; stars 1-5 enforced in the database
- creation locks the `CompletedDeal` row so the second of two concurrent submissions always sees the first; there is no update or delete API
- the reciprocal (second) rating emits `MUTUAL_RATING_COMPLETED`; deal ratings never emit `RATING_CREATED`, do not change `avg_rating`/`ratings_count`, and trigger none of the public rating rewards
- `DEAL_RATING` permission resource: customers and sellers have `create` and `retrieve`; managers have no access
- no backfill from existing `SellerRating` rows

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
- customer profiles can soft-delete their own comments; seller profiles cannot self-delete comments
- managers with `comments.delete` can soft-delete any asset or seller comment
- comments do not support replies, reporting, likes, notifications, or analytics

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
- managers with `ratings.delete` can soft-delete asset and seller ratings
- rating deletion recomputes cached aggregate fields and soft-deletes an inline rating comment when it exists
- customer and seller profiles cannot self-delete ratings
- rating update, replies, reports, likes, notifications, and analytics are not implemented

### `apps.races`

Current responsibilities:

- race/report records for pigeon or bird racing
- race result records with denormalized competitor and bird ring details
- automatic race result rank recalculation
- global race-result search

Current race API:

- `/api/races/`
  - `GET`: authenticated paginated race list
  - `POST`: manager-only race creation
- `/api/races/<id>/`
  - `GET`: authenticated race detail with nested results
  - `PUT/PATCH/DELETE`: manager-only race updates and deletion
- `/api/races/<id>/results/`
  - `GET`: authenticated paginated results for one race ordered by rank
  - `POST`: manager-only bulk result creation for one race
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
- creating, updating, deleting, or bulk-creating results recalculates all ranks for the race inside transactions
- active `RaceResult` rows enforce unique `bird_ring_number` per race and unique `rank` per race
- `speed` and `distance_km` must be greater than zero
- `arrival_datetime` must be after the parent race `release_datetime`
- global result search supports `bird_ring_number`, `competitor_name`, `q`, and `race_id`
- `include_race=true` on race-result list/detail responses includes a nested race summary; false or omitted excludes it
- race APIs use `HasAppAccess` with `allow_scoped_read_access=True`
- managers can write races and results; authenticated sellers and customers can read races, per-race results, and race-result search

### `apps.packages`

Current responsibilities:

- manager-created seller package plans
- seller-owned package request records
- asset seller package and pedigree seller package model foundations
- manager activation and cancellation of seller package requests
- package point consumption for seller-created auctions and product assets
- package usage log records for successful seller package point consumption

Important note:

This app is not a payment, wallet, invoice, recharge, or auto-renewal system. Managers manually activate seller packages. Package point consumption is integrated for seller-created auctions and product assets. Pedigree package consumption remains planned and is not implemented yet.

Current package API:

- `/api/packages/`
  - `GET`: sellers list available packages only; managers list all packages
  - `POST`: manager creates a package plan
- `/api/packages/manager/packages/<id>/`
  - `PATCH`: manager updates package plan fields
- `/api/packages/my-packages/`
  - `GET`: seller lists own package history; supports `status` and `activated` filters
  - `GET ?active=true`: seller retrieves current active package summary
  - `POST`: seller requests a package by `package_id`
- `/api/seller-packages/`
  - `GET`: manager lists seller package requests/history; supports real `status` values such as `pending`, `active`, `exhausted`, and `cancelled`
- `/api/seller-packages/<id>/`
  - `PATCH`: manager status transition endpoint; accepts `active` or `cancelled`

Current package behavior:

- `Package` is a plan/template and is not seller-owned
- package numeric rules require positive points, activation period, auction cost, and product cost; non-negative price; and costs not exceeding package points
- package serializer validation lives in `PackageSerializer.validate()`; do not add a separate serializer-validator wrapper for package fields
- seller package requests create `AssetSellerPackage` rows by default
- `PedigreeSellerPackage` exists as a thin concrete model foundation but has no request or consumption integration yet
- requested seller packages start as `pending`, `activated=False`, with `remaining_points` copied from the referenced package
- manager activation sets the new package to `active`, `activated=True`, `activated_at`, and `expires_at`
- manager activation does not deactivate or invalidate other active seller packages owned by the same seller
- a seller can have multiple active, activated, non-expired seller packages
- seller packages expose room-like public fields: `display_name`, `description`, and `is_public`
- manager cancellation is allowed only for pending seller packages and sets `cancelled`, `activated=False`, and `invalidated_at`
- there is no standalone deactivate endpoint; deactivation currently happens through cancellation, exhaustion, or expiry invalidation
- `SellerPackageService.get_active_package()` validates `expires_at`; expired active packages are marked `exhausted`, `activated=False`, and invalidated before returning a not-found response
- `PackageService.consume_points()` supports `auction_created` and `product_created` only; `pedigree_created` is rejected as unsupported for now
- consumption uses `transaction.atomic()` and `select_for_update()` on the selected `SellerPackage`
- auction and seller product creation must provide `seller_package_id`; the backend does not auto-select the first active package
- only a selected seller-owned, active, activated, unexpired `SellerPackage` can be consumed
- package costs come from `Package.auction_cost` and `Package.product_cost`
- successful consumption decrements `SellerPackage.remaining_points` and writes one `PackageUsageLog`
- a package becomes `exhausted`, `activated=False`, and gets `invalidated_at` when remaining points reach `0` or are below both auction and product creation costs
- expired active packages are marked exhausted/invalidated before the validation error is returned
- missing selected packages, insufficient points, and unsupported usage types do not write usage logs and do not decrement points
- package consumption is internal only; there is no public consume, recharge, manual exhaust, or deactivate endpoint
- `PackageUsageLog` stores successful consumption traces using a seller package FK, seller FK, usage type, points used, and optional string/id related object reference
- no `GenericForeignKey` is used for package usage logs

### `apps.loyalty`

Current responsibilities:

- user-level point wallets
- immutable point transaction ledger records
- optional profile context on point transactions
- profile-level badge awards
- static badge definitions represented by code choices and constants, not database rows
- an event-driven rewards engine for selected synchronous rewards and trusted buyer/seller aggregate task entry points

Important note:

This app implements an MVP rewards engine, not a campaign platform. It does not implement withdrawal, payment gateway behavior, notification preferences, paid badges, shipping/receipt rewards, or a full scheduled reward campaign system. Marketplace discount and cashback offers live in `apps.promotions`, not `apps.loyalty`; Lucky Wheel lives in `apps.lucky_wheel`, referrals live in `apps.referrals`, and seasons live in `apps.seasons`.

Current loyalty API:

- `/api/loyalty/points/`
  - `GET`: authenticated user retrieves their user-level point balance
- `/api/loyalty/points/buy-with-cashback/`
  - `POST`: active customer converts cashback into PP Coins atomically at 20 PP per 1 cashback unit
- `/api/loyalty/points/transactions/`
  - `GET`: authenticated users list their own account point transactions; managers list all transactions by default
- `/api/loyalty/badges/`
  - `GET`: without `profile_id`, returns the static badge choice catalog
  - `GET ?profile_id=<id>`: manager-only badge award list for a specific profile
  - `POST`: manager-only badge award creation for a specific profile and badge type
- `/api/loyalty/badges/my/`
  - `GET`: authenticated user retrieves active badge awards for the current active profile
  - `GET ?include_expired=true`: authenticated user includes expired historical badge awards
- `/api/loyalty/admin/reward-rules/`
  - `GET`: manager lists every system-defined reward rule with effective values, with `reward_rules.list`
- `/api/loyalty/admin/reward-rules/<code>/`
  - `GET`: manager retrieves one reward rule by code, with `reward_rules.retrieve`
  - `PATCH`: manager tunes `enabled`, `points`, `multiplier`, `threshold`, or `configuration`, with `reward_rules.update`; there is no create or delete

Current point behavior:

- `PointWallet` belongs to `UserAccount`, not `Profile`
- each user can have one wallet
- wallets are created lazily by `PointService.get_or_create_wallet()`
- wallet balance is a non-negative integer
- `PointTransaction` belongs to both `PointWallet` and `UserAccount`
- `PointTransaction.profile` is optional and stores the active profile context that caused the transaction when applicable
- point transaction types are `earn`, `spend`, and `expire`
- reward source constants include `onboarding`, `lucky_wheel`, `auction_completed`, `order_completed_amount` and `order_completed_bonus` (pre-BE-REW-08 historical rows only), `deal_completed`, `deal_completion_bonus`, `successful_sale`, `mutual_rating`, `referral_approved`, `referral_first_bid`, `active_auction_bidder`, `auction_posted`, `rating_received`, `referral_accepted`, `first_bid_completed`, `season_winner`, `cashback_points_purchase`, and `manager_adjustment`
- point transactions require positive points, non-negative `balance_after`, and a non-empty reason
- `PointService.earn_points()`, `spend_points()`, and `expire_points()` run in DB transactions and lock the wallet row before changing balance
- spending and expiring points cannot make wallet balance negative
- optional nonblank `idempotency_key` values are unique across point transactions
- reusing an idempotency key for the same logical transaction returns the existing transaction without changing balance again
- reusing an idempotency key for a different logical transaction raises a validation error
- creating a new positive `earn` `PointTransaction` (`points > 0`) atomically creates one `points_earned` congratulatory `Notification` (`idempotency_key=f"points_earned:{point_tx.id}"`, BE-NOT-01); duplicate idempotent `earn_points()`, `spend_points()`, and `expire_points()` calls create `0` notifications
- non-manager transaction lists are always scoped to `request.user`
- manager transaction lists are unscoped by default and support `user_id`, `profile_id`, `transaction_type`, and `source_type` filters

Current badge behavior:

- there is no `Badge` model
- `BadgeAward` belongs to `Profile`, not `UserAccount`
- `BadgeAward.badge_type` is a fixed choice field
- `BadgeAward.expires_at` supports temporary badges
- `BadgeAward.revoked_at` supports manual invalidation if needed
- `BadgeAward.idempotency_key` is unique when provided and prevents duplicate event awards
- current badge types are:
  - `first_order`
  - `points_starter`
  - `seller_starter`
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
- badge metadata, names, descriptions, eligibility, criteria type, criteria value, and icon URL live in `apps.loyalty.constants`
- badge definitions are presentation/eligibility only and never define PP Coins; events grant points (`RewardEngine._grant_points`, amounts from `RewardRuleService` or explicit `*_POINTS` constants) and badges (`RewardEngine._award_badge`) separately, or both explicitly via `_grant_points_and_badge`
- welcome badges are badge-only; the badge catalog `criteria_value` contains no `points`, and deal badges use neutral completed-deal wording
- `BadgeAwardCreateSerializer` owns submitted `badge_type` choice validation for the manager award API
- badge eligibility is enforced by `BadgeService.award_badge()`
- `first_order` is eligible for `Customer` profiles
- `seller_starter` is eligible for `Seller` profiles
- `points_starter` is eligible for any profile type
- manager-created badge awards remain idempotent per profile and badge type through `BadgeService.award_badge()`
- reward-created badges use deterministic idempotency keys and service-level one-time or active-temporary checks
- badge awards atomically create one `badge_awarded` congratulatory `Notification` (`idempotency_key=f"badge_awarded:{award.id}"`, BE-NOT-01) only when a new `BadgeAward` row (`created_new=True`) is created; returning an existing idempotent award does not create another notification
- temporary badges are active only when `revoked_at IS NULL` and `expires_at` is null or in the future
- expired badges are retained as history and hidden from active badge reads by default
- different profiles under the same user can receive the same badge type independently
- `/api/loyalty/badges/my/` returns only the current active profile's active badge awards unless `include_expired=true`
- `RewardEngine.handle_event()` is the central synchronous reward dispatcher
- reward idempotency keys use `reward:{reward_code}:{event_type}:{source_type}:{source_id}:profile:{profile_id}`
- the 100 PP onboarding reward is account-level: `RewardEngine.grant_account_onboarding_reward()` awards it once per `UserAccount` with key `reward:onboarding:user:{user_id}` and `source_type=onboarding`, attributed to the first triggering profile; later customer/seller profile creation awards `+0` onboarding PP
- the onboarding amount comes from the `ACCOUNT_ONBOARDING` reward rule (default 100 PP); if that rule is disabled or pays 0, no onboarding PP is awarded
- reward rule values resolve through `RewardRuleService`: an optional `RewardRuleConfig` override row, otherwise the canonical default in `DEFAULT_REWARD_RULES`; codes are the closed `RewardRuleCode` list, there is no seed data migration, and config changes never rewrite existing `PointTransaction` rows
- `ACCOUNT_ONBOARDING`, `ACTIVE_AUCTION_BIDDER`, `DEAL_COMPLETED`, `DEAL_COMPLETION_BONUS`, `SUCCESSFUL_SALE`, `MUTUAL_RATING`, `REFERRAL_APPROVED`, and `REFERRAL_FIRST_BID` are currently read by backend code; the other rule codes (`AUCTION_PUBLISHED`, `POSITIVE_RATING`) are configuration only and marked `is_implemented=false`
- on each valid `BID_PLACED`, if the auction's non-deleted customer bids (across all its items) come from at least the `ACTIVE_AUCTION_BIDDER` threshold of distinct `UserAccount`s (default 5), every such account is paid the rule's points (default 50 PP) once per auction, points only, with key `reward:active_auction_bidder:auction:{auction_id}:user:{user_id}` and source `active_auction_bidder`; later new bidders are paid on their own bid; no historical backfill
- when both parties of a `CompletedDeal` have a `DealRating`, `MUTUAL_RATING_COMPLETED` pays buyer and seller the `MUTUAL_RATING` rule points (default 20 PP each) once per deal, points only, keys `reward:mutual_rating:deal:{deal_id}:{buyer|seller}`, source `mutual_rating` with the deal id; a disabled rule pays nothing and is not topped up later
- `WELCOME_BUYER` and `WELCOME_SELLER` remain profile-specific badges and carry no points; legacy pre-BE-REW-02 welcome-badge point transactions count as proof that the account was already onboarded
- implemented synchronous reward events include account onboarding (`account_onboarded`), customer profile creation, seller profile creation, auction creation/publishing, market product listing, bid placement, deal completion (`deal_completed`), rating creation, and seller follow creation/reactivation
- sale-completion rewards run only from `DEAL_COMPLETED`, which `CompletedDealService` emits once when it records a new `CompletedDeal` (currently after seller approval of an `AuctionPaymentRequest` or `MarketPaymentRequest`); `RewardEngine` reads the deal, never `apps.payments`
- `auction_payment_request_approved` and `market_payment_request_approved` are no longer dispatched as events; they remain only as labels inside historical reward idempotency keys
- deal rewards confirmed by a payment request keep the historical identity (`reward:{code}:{legacy_event}:PaymentRequest:{payment_request_id}:profile:{id}`, same `source_type`/`source_id`/metadata), rebuilt from `CompletedDeal.metadata.payment_request_id`, so no historical sale can be rewarded twice
- auction posting now also awards 50 PP with source `auction_posted`
- every completed deal (market and auction) pays both buyer and seller `floor(CompletedDeal.amount × rate) × multiplier` PP from the `DEAL_COMPLETED` rule (default multiplier 2; rates in its configuration as `points_per_unit_<CURRENCY>`, default EGP 1 and USD 1); a currency without a rate earns no deal points (never assumed 1:1), is logged as a warning, and is reported in the event result `skipped` list
- every completed deal also pays both parties the separate `DEAL_COMPLETION_BONUS` rule (default 100 PP, not multiplied, not badge-coupled)
- every completed deal also pays the seller only the separate `SUCCESSFUL_SALE` rule (default 100 PP, recurring, points only, key `reward:successful_sale:{source_type}:{source_id}:seller`, source `successful_sale`); it is independent of the one-time first-sale incentives and follows the same first-processing rule (no backfill or top-up)
- deal reward keys are `reward:completed_deal:{source_type}:{source_id}:{buyer|seller}` and `reward:completed_deal_bonus:...`; both are paid only on a deal's first reward processing (no recalculation, compensation, or backfill), so deals rewarded under the pre-BE-REW-08 market formula (`points:order_item:{id}:{role}:amount|bonus`) are never paid again
- a completed auction deal grants the winning customer one Lucky Wheel attempt and a one-time 200 PP `first_bid_completed` reward
- first-sale badges (`FIRST_AUCTION_SALE_CONFIRMED`, `FIRST_MARKET_SALE_CONFIRMED`) pay only for the seller's first recorded deal of that type and only if the seller does not already hold the badge (protects sellers whose first sale predates `CompletedDeal`)
- receiving an asset or seller rating awards 30 PP to the rated profile owner with source `rating_received`
- `OrderItem` seller approval, payment request creation/update/rejection, cashback redemption/earning, complaint creation, shipping, handoff, and buyer receipt confirmation do not grant sale-completion rewards
- `LAST_MINUTE_FIGHTER` is a 7-day temporary badge and uses the bid's pre-auto-extend final-window state
- `TRUSTED_BUYER` and `TRUSTED_SELLER` aggregate checks count `CompletedDeal` rows per buyer/seller profile; they are Celery task entry points enqueued with `transaction.on_commit()` after a deal completes
- **Permanent Buyer & Seller Levels Foundation (`BE-LTY-01A`, status `PARTIAL / BLOCKED`)**:
  - **Confirmed level rules**: Buyer (`CustomerProfile`) and Seller (`SellerProfile`) levels are separate, coexist independently on the same `UserAccount`, are permanent (never downgrade), and never reset (monthly, yearly, by `Season`, when PP Coins are spent, or when active profile switches). `PointWallet` remains `UserAccount`-owned.
  - **Confirmed Seller Level Table (`SELLER_LEVEL_TABLE`)**: `100` -> `حساب موثّق` (`شارة + ثقة`), `500` -> `بائع معتمد` (`شارة + ترتيب أعلى`), `1,000` -> `بائع ذهبي` (`حجل ذهبي + إبراز`), `5,000` -> `بائع نخبة` (`Banner + عمولة أقل`), `10,000+` -> `أسطوري` (`توثيق دائم + دعم P`); `< 100` has no earned level (`None`).
  - **Confirmed Buyer Level Table (`BUYER_LEVEL_TABLE`)**: `100` -> `مشتري موثوق` (`مزايدة بدون عربون`), `500` -> `مشتري نشط` (`خصم 2%`), `1,000` -> `مشتري ذهبي` (`كاش باك 1%`), `5,000` -> `مشتري EP` (`مهلة سداد أطول`), `10,000+` -> `Elite Buyer` (`دعوات خاصة + شحن مجاني`); `< 100` has no earned level (`None`).
  - **Implemented level calculation**: `BuyerLevelService` and `SellerLevelService` (`apps/loyalty/services.py`) provide `resolve_level_for_total()`, `get_completed_deal_totals_by_currency()` (summing immutable `CompletedDeal.amount` per currency across both `MARKET` and `AUCTION` deals, with 0 migrations needed because `CompletedDeal` cannot be updated or deleted), and `evaluate_permanent_level(profile, canonical_currency=...)`.
  - **Blocked currency unit (`LEVEL METRIC CURRENCY UNIT — BLOCKED`)**: Because `CompletedDeal.currency` supports 20 `CurrencyOptions` (`EGP`, `USD`, `SAR`, `KWD`, etc.) plus blank `""` and PPW has no cross-currency normalization or confirmed canonical currency unit for `[100, 500, 1000, 5000, 10000]`, automatic multi-currency or unconfigured-currency threshold evaluation returns `is_blocked=True` (`blocker_code="LEVEL METRIC CURRENCY UNIT — BLOCKED"`) and never adds different currencies together or guesses a conversion rate. Legacy `completed_orders_count`-based serializer fields (`seller_level`, `customer_level`, `level_discount_percent`, `level_cashback_percent`) remain untouched for backward compatibility.
  - **Display-only benefits vs blocked benefit effects (`LEVEL BENEFIT — BLOCKED`)**: All 10 product-stated level benefit labels are stored as display-only metadata on `SELLER_LEVEL_TABLE` / `BUYER_LEVEL_TABLE`; operational enforcement of every benefit (`شارة + ثقة`, `شارة + ترتيب أعلى`, `حجل ذهبي + إبراز`, `Banner + عمولة أقل`, `توثيق دائم + دعم P`, `مزايدة بدون عربون`, `خصم 2%`, `كاش باك 1%`, `مهلة سداد أطول`, `دعوات خاصة + شحن مجاني`) is `BLOCKED` pending explicit product rules. No level-up `Notification` is emitted.
- `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md` is the canonical rewards architecture/status document; the original MVP implementation report lives at `docs/rewards/rewarding-system-report.md`

### `apps.lucky_wheel`

Current responsibilities:

- user-account Lucky Wheel attempt grants
- backend-selected weighted spin results
- prize inventory counters
- idempotent spin records
- delegation to PP Coin, badge, and user promotion grant services

Current Lucky Wheel API:

- `/api/lucky-wheel/current/`
  - `GET`: authenticated user reads eligibility, remaining attempts, active display segments, active attempt grants, and active user promotion grants
- `/api/lucky-wheel/spin/`
  - `POST`: authenticated user spends one attempt using a required `idempotency_key`; backend chooses the prize
- `/api/lucky-wheel/spins/my/`
  - `GET`: authenticated user lists own spin history
- `/api/lucky-wheel/admin/prizes/`
  - `GET` / `POST`: manager prize configuration with `lucky_wheel.prizes.list` or `lucky_wheel.prizes.create`
- `/api/lucky-wheel/admin/prizes/<id>/`
  - `PATCH`: manager prize update with `lucky_wheel.prizes.update`
- `/api/lucky-wheel/admin/spins/`
  - `GET`: manager spin listing with `lucky_wheel.spins.list`; includes `user_id`, `profile_id`, and `username`

Current Lucky Wheel behavior:

- PP Coin wallet remains user-account-level; spin profile is only the context profile
- fresh customer/seller onboarding no longer grants a Lucky Wheel attempt (`BE-LW-01`) or a weekly onboarding draw entry (`BE-DRW-CANCEL-01`); `LuckyWheelGrantSourceType.ONBOARDING` and `LuckyWheelGrantService.grant_onboarding_attempt` remain for historical records only
- a completed auction deal grants the winner one idempotent `auction_completed` attempt (current implementation / technical decision supporting Scenario C-09 auction excitement)
- `GET /api/lucky-wheel/current/` delegates to `LuckyWheelService.get_availability()` (read-only) and exposes `available`, `eligible`, `has_active_wheel`, `remaining_attempts`, `cooldown_until`, `segments` (`id`, `label`, `description`, `prize_type`, `points_amount`, `badge_type`), `active_grants` (`id`, `source_type`, `total_attempts`, `remaining_attempts`, `expires_at`, `is_active`, `created`), and `user_promotion_grants`; `available`/`eligible` are `True` only when both `has_active_wheel` (at least one active, non-exhausted prize segment) and `remaining_attempts > 0` hold, and internal `metadata`, `source_id`, `idempotency_key`, `weight`, and inventory fields are omitted from public responses (BE-LW-01 / BE-LW-01A)
- `LuckyWheelGrantService.active_grants()` orders usable grants by `F("expires_at").asc(nulls_last=True), "created", "id"` (`expires_at ASC NULLS LAST, created ASC, id ASC`) so expiring grants are always consumed before non-expiring grants across both PostgreSQL and SQLite (BE-LW-01A)
- `LuckyWheelGrantService.grant_attempt()` and `LuckyWheelSpinService.spin()` lock `UserAccount` (and the consumed `LuckyWheelGrant` on spin) with `select_for_update()` inside `transaction.atomic()`, wrap `LuckyWheelGrant.objects.create()` in an inner `transaction.atomic()` savepoint with full user/source/source_id collision validation on `IntegrityError` recovery, reject cross-user `idempotency_key` collisions, and exclude expired grants (`expires_at <= now`) (BE-LW-01 / BE-LW-01A)
- `LuckyWheelSpinRequestSerializer` accepts only `idempotency_key` and rejects any client-supplied `prize_id`, `prize`, `outcome`, `status`, `slot`, `segment`, `weight`, `seed`, `random_value`, `points_amount`, `discount_offer`, `cashback_offer`, `badge_type`, `grant_id`, `remaining_attempts`, or `eligible` field with `400`; the backend chooses from active, non-deleted, non-exhausted weighted prizes (`weight > 0`) ordered by `id` via `LuckyWheelPrizeSelector.select()` using `secrets.SystemRandom()` (`LuckyWheelPrize.weight` is a current implementation / technical decision, BE-LW-02)
- `LuckyWheelSpinService._choose_prize()` locks the selected `LuckyWheelPrize` row with `select_for_update()`, re-verifies `is_active`, `deleted is None`, `weight > 0`, and `quantity_awarded < quantity_total` under lock, and boundedly re-resolves from remaining valid candidates if a finite prize was concurrently exhausted or deactivated before lock acquisition (raising `400` and rolling back attempt consumption if no candidates remain, BE-LW-02)
- concrete backend prize types are `pp_coins`, `discount_offer`, `cashback_offer`, `badge`, and `no_prize` (implementing the generic `Random points/discount/prize` product concept); `LuckyWheelPrize.clean()` enforces strict one-of reward payload fields per `prize_type`, positive `weight > 0`, and `quantity_total >= quantity_awarded` (BE-LW-02)
- discount/cashback prizes require offers whose `trigger_type` is `lucky_wheel` and verify at spin time that the target `DiscountOffer` / `CashbackOffer` is still active and non-deleted; optional `prize.metadata["validity_days"]` sets `UserPromotionGrant.expires_at` (`awarded_at + timedelta(days=validity_days)`), while omitted `validity_days` preserves `expires_at=None` (BE-LW-02)
- discount/cashback prizes create `UserPromotionGrant` rows instead of applying to every customer automatically or directly mutating `cashback_balance` on spin
- `LuckyWheelSpin.prize_snapshot` captures immutable prize and offer display fields (`id`, `label`, `description`, `prize_type`, `points_amount`, `discount_offer_id`, `discount_offer`, `cashback_offer_id`, `cashback_offer`, `badge_type`) unaffected by later manager edits or deactivation (BE-LW-02)
- PP Coin, promotion grant, and badge rewards use spin-id-based idempotency keys, and any failure during prize target validation or reward application rolls back attempt decrement, inventory increment, and spin creation atomically (BE-LW-02)
- winning spins (`pp_coins`, `discount_offer`, `cashback_offer`, and `badge`) also record one `DIGITAL_AUTOMATIC` / `FULFILLED` `PrizeAward` in `apps.prizes` (`source_type=LUCKY_WHEEL`, `source_id=str(spin.id)`, key `prize:source:lucky_wheel:spin:{spin_id}`) inside the same transaction; `no_prize` spins create 0 `PrizeAward` rows (BE-PRZ-01)

### `apps.referrals`

Current responsibilities:

- one-time link-sharing referral codes (`LinkSharingCode`)
- new-account referral redemption into a pending `Referral`
- manager approval or rejection of referrals (anti-bot review)
- inviter PP Coin reward after approval (through `REFERRAL_APPROVED`)
- inviter PP Coin reward on the approved referred account's first bid (through `REFERRAL_FIRST_BID`, evaluated by `RewardEngine` on `BID_PLACED`)
- monthly referral challenges (`ReferralChallenge`, `ReferralChallengePrize`, `ReferralChallengeWinner`): account-level scoring of approved referrals, Top 10 leaderboard, countdown, personal progress, manager finalization into Top 3 winner snapshots

Current referral API:

- `/api/referrals/codes/`
  - `POST`: authenticated active profile creates a code expiring after 24 hours
- `/api/referrals/redeem/`
  - `POST`: authenticated active profile redeems a code when the account is at most 2 hours old; responds with the code fields plus `referral` (`id`, `status`, `referred_profile_id`, `redeemed_at`); pays nothing
- `/api/referrals/my/`
  - `GET`: authenticated user lists referrals their account invited, with `status`; supports `status` filter; no reviewer details
- `/api/referrals/admin/`
  - `GET`: manager lists referrals with `referrals.list`; filters `status`, `inviter_user`, `referred_user`
- `/api/referrals/admin/<id>/approve/`
  - `POST`: manager approves a pending referral with `referrals.approve`; repeating returns the approved referral unchanged
- `/api/referrals/admin/<id>/reject/`
  - `POST`: manager rejects a pending referral with `referrals.reject` and optional `reason`; repeating returns it unchanged
- `/api/referrals/challenge/current/`
  - `GET`: authenticated user reads the active challenge (else the nearest upcoming one; 404 when neither, never a review or finalized one) with `status`, `starts_at`, `ends_at`, `server_time`, `seconds_remaining`, `seconds_until_start`, `top_10` (`rank`, `username`, `challenge_score`, `is_me`), `me` (`challenge_score`, `rank`, `leader_score`, `gap_to_first`), and `prizes` (`rank`, `prize_code`, `title`)
- `/api/referrals/admin/challenges/`
  - `GET`: manager lists challenges with `referral_challenges.list`
  - `POST`: manager creates a challenge (`name`, `starts_at`, `ends_at`, optional `prizes`) with `referral_challenges.create`
- `/api/referrals/admin/challenges/<id>/`
  - `GET`: manager reads a challenge with its live leaderboard (null once finalized), `pending_referrals_count`, prizes, and winners with `referral_challenges.retrieve`
  - `PATCH`: manager edits it with `referral_challenges.update`
- `/api/referrals/admin/challenges/<id>/finalize/`
  - `POST`: manager freezes the Top 3 winners with `referral_challenges.finalize`; repeating returns the existing result

Current referral behavior:

- `LinkSharingCode.is_used` means only that the code was consumed; `Referral.status` (`pending` / `approved` / `rejected`) is the verified relationship
- referral codes are locked on redeem and can be used once; users cannot redeem their own account/profile code; the 24-hour TTL and 2-hour new-account window are unchanged
- each code yields at most one `Referral` (one-to-one) and each referred `UserAccount` can be referred only once, ever (unique `referred_user` over all rows); a rejected account cannot redeem another code, and other profiles of the same account cannot claim again
- `Referral` stores `inviter_user` / `inviter_profile` and `referred_user` / `referred_profile`, `redeemed_at`, `reviewed_at`, `reviewed_by` (user account), and `rejection_reason`
- redemption pays no PP; approval (locked, pending only) emits `REFERRAL_APPROVED`, which pays the inviter the `REFERRAL_APPROVED` rule points (default 30 PP) once per referral, key `reward:referral_approved:referral:{referral_id}:inviter`, source `referral_approved`; rejection pays nothing; the referred user gets nothing
- if the rule is disabled at approval, the referral is still approved and is not topped up later
- `successful_invites_count` on profile serializers counts `approved` referrals of the inviter profile, not consumed codes
- legacy used codes from before admin approval were backfilled reward-silently as `approved` referrals (earliest per referred account); their old `points:referral:{code_id}:inviter:{profile_id}` row marks them as already paid
- on a valid regular `BID_PLACED`, if the bidder's account has a referral that was already `approved` when the bid was placed and this is the account's earliest bid ever (all its profiles, auctions, and items, soft-deleted bids included; buy-now rows, which never emit `BID_PLACED`, are not bid history), the inviter is paid the `REFERRAL_FIRST_BID` rule points (default 200 PP) once per referral, points only, key `reward:referral_first_bid:referral:{referral_id}:inviter`, source `referral_first_bid`, `user`/`profile` = `inviter_user`/`inviter_profile`; the referred bidder gets nothing from it
- the first-bid referral reward is not retroactive: a bid placed while the referral was pending, a bid made while the rule was disabled, or bids that predate BE-REF-02 never pay later, and approval does not look back at earlier bids; there is no backfill
- referral redemption and approval do not create a Lucky Wheel attempt
- whether clients show the referral share link is controlled by the display-only `AppSettings` referral share flag (`GET /api/app-settings/`); referral APIs do not check it
- a challenge's score for an account is the count of its `approved` referrals (grouped by `Referral.inviter_user`, so customer and seller modes add up) with `starts_at <= redeemed_at < ends_at`; raw shares, clicks, consumed codes, pending, and rejected referrals never count; `redeemed_at` picks the challenge and approval (even during review) decides whether it counts; challenge score is not PP and creates no point transactions
- challenge status is derived, not stored: `draft` before `starts_at`, `active` inside the window, `review` after `ends_at` until finalized, `finalized` once `finalized_at` is set; periods cannot overlap (adjacent periods are allowed) and `starts_at < ends_at` is a DB constraint
- ranking is higher score, then the earlier time the account reached that score (latest counted `redeemed_at`), then lower account id (technical tie-break); only accounts with a score appear, Top 10 only
- dates and prizes are editable only while `draft`; `active` and `review` challenges accept only `name`; finalized challenges are read-only; supplied `prizes` replace the set (ranks 1-3, codes `IPHONE`, `AIRPODS`, `ANNUAL_LOFT_SUBSCRIPTION`; default Top 3 prizes are created when none are supplied)
- finalization locks the challenge, requires `now >= ends_at` and no pending referral redeemed in the window, then writes up to three immutable `ReferralChallengeWinner` rows (score, tie-break time, prize FK plus copied code and title) and sets `finalized_at` / `finalized_by`; winners are never recalculated
- finalization also records a `CLAIM_REQUIRED` / `PENDING` `PrizeAward` in `apps.prizes` (`source_type=REFERRAL_CHALLENGE`, `source_id=str(winner.id)`, key `prize:source:referral_challenge:winner:{winner_id}`) for canonical non-digital in-kind winners (`IPHONE`, `AIRPODS`) inside the same transaction, which atomically triggers a `prize_awarded` congratulatory notification (BE-PRZ-01, BE-PRZ-02 & BE-NOT-01); winners claim and managers review/deliver those awards via `PrizeClaim` in `apps.prizes`, while `ANNUAL_LOFT_SUBSCRIPTION` fulfillment provisioning remains `BLOCKED` (`BE-PRZ-03`, pending product/config decisions on `UserAccount` vs `Profile` subscription recipient, `PedigreeTemplatePackage` tier and `selected_template_ids`, add-ons/AI quota, and non-nullable `LoftSubscription.invoice` provisioning — neither falsely marked `FULFILLED` nor classified as a physical shipment claim) and challenge PP payouts are not implemented

- `successful_invites_count` still counts approved referrals per inviter profile, unlike the account-level challenge score (known inconsistency)

### `apps.seasons`

Current responsibilities:

- reward season records
- configurable per-season Rank 1..3 prize definitions (`SeasonPrize`)
- seller insight snapshots for a season window
- deterministic winner ranking and idempotent PP Coin awards

Current seasons API:

- `/api/seasons/`
  - `GET` / `POST`: manager list/create with `seasons.list` or `seasons.create`
- `/api/seasons/<id>/`
  - `PATCH`: manager update with `seasons.update`
- `/api/seasons/<season_pk>/prizes/`
  - `GET` / `POST`: manager list/create season rank prizes with `seasons.prizes.list` or `seasons.prizes.create`
- `/api/seasons/<season_pk>/prizes/<id>/`
  - `PATCH` / `DELETE`: manager update/delete a season rank prize before season start with `seasons.prizes.update` or `seasons.prizes.delete`
- `/api/seasons/<id>/calculate-winners/`
  - `POST`: manager calculates and ranks seller season insight snapshots without awarding points (`seasons.calculate`)
- `/api/seasons/<id>/finalize/`
  - `POST`: manager finalizes an ended season (`timezone.now() >= ends_at`), refreshes latest scores one final time, freezes rankings, snapshots winning prizes, and awards Top-3 winners once (`seasons.finalize`)
- `/api/seasons/<id>/seller-insights/`
  - `GET`: manager reads snapshot insights with `seasons.insights.list`

Current seasons behavior:

- seasons require `starts_at < ends_at`
- creating a new `Season` automatically creates default `SeasonPrize` rows for Ranks 1..3 (`100000`, `50000`, and `25000` PP, BE-SEA-03)
- `SeasonPrize` configuration (`rank` 1..3, `pp_points >= 0`, optional `symbolic_prize_code`, `title`, and `metadata`) is editable only while `season.status == draft` and `timezone.now() < season.starts_at`; once a season starts, is calculated, or is finalized, prize configuration is frozen
- seller qualification requires at least 1 `CompletedDeal` in `[starts_at, ends_at)` (`SeasonScoreService`, BE-SEA-01)
- composite `season_score` (0..100, `Decimal("0.01")` `ROUND_HALF_UP`) weights normalized completed sales (`40%`), buyer-to-seller deal ratings (`30%`), seller reliability (`20%`), and verified-buyer chat reply rate (`10%`)
- ranking sorts by `season_score` descending, then `SellerProfile.id` ascending
- score calculation (`SeasonScoreService` / `calculate-winners`) is pure and creates 0 PP, 0 badges, 0 prizes, and 0 notifications
- finalization (`SeasonService.finalize` / `SeasonPayoutService`, BE-SEA-02, BE-SEA-03 & BE-PRZ-01) requires `timezone.now() >= ends_at`, locks the season, refreshes scores one final time, verifies that every qualifying Top-3 rank has a configured `SeasonPrize` before any payout executes, snapshots the winning prize onto `SeasonSellerInsight.metadata["season_prize"]`, awards configured `pp_points` (`> 0`) once via `PointService.earn_points` (`source_type=season_winner`, rank-independent key `reward:season_winner:season:{season_id}:seller:{seller_id}`), records a corresponding `DIGITAL_AUTOMATIC` / `FULFILLED` `PrizeAward` in `apps.prizes` (`source_type=SEASON`, `source_id=str(insight.id)`, key `prize:source:season:{season_id}:seller:{seller_id}`), and sets `status=finalized`, `finalized_by`, and `finalized_at`
- historical `season_winner` transactions (including legacy rank-based keys) are honored as already paid without top-ups, clawbacks, duplicate payouts, or retroactive `PrizeAward` backfill
- once finalized, season rankings, `starts_at`/`ends_at`, and `SeasonPrize` rows are immutable and recalculation is rejected

### `apps.draws`

Current product status (`BE-DRW-CANCEL-01`):

- **CURRENT PRODUCT DECISION: Weekly onboarding draw cancelled.**
- the weekly onboarding draw (`BE-DRW-01` / `BE-DRW-02`) is cancelled completely by product decision
- `WeeklyDraw` and `WeeklyDrawEntry` remain in `apps.draws` only as dormant historical schema records so existing migration history (`apps/draws/migrations/0001_initial.py`) stays intact without destructive table drops (`0` new migrations)
- no `/api/draws/...` runtime routes are mounted (`ppw/urls.py` does not include `apps.draws.urls`) and no `draws.weekly.*` manager permissions are registered
- customer registration (`POST /api/auth/register/customer/`), seller profile creation (`POST /api/users/mobile/sellers/`), customer profile creation (`POST /api/users/mobile/customers/`), and profile switching (`POST /api/auth/switch-profile/`) create `0` `WeeklyDrawEntry` rows
- `WeeklyDrawEntryService.ensure_for_onboarding` and `WeeklyDrawEntryService.ensure_for_first_profile_onboarding` are side-effect-free no-ops returning `None`
- no weekly onboarding draw winners, `PrizeAward` rows, `PointTransaction` rows, or `Notification` rows are created

### `apps.prizes`

Current responsibilities:

- shared `UserAccount`-owned prize award ledger (`PrizeAward`) across integrated reward sources (`SEASON`, `REFERRAL_CHALLENGE`, `LUCKY_WHEEL`)
- explicit fulfillment classification (`DIGITAL_AUTOMATIC` vs `CLAIM_REQUIRED`) and status tracking (`FULFILLED` vs `PENDING`)
- idempotent, savepoint-safe prize recording (`PrizeAwardService`) without double-executing underlying digital rewards
- claim submission (`PrizeClaim`) and manager review/delivery lifecycle (`PrizeClaimService`, `SUBMITTED -> REVIEWED -> DELIVERED`) for `CLAIM_REQUIRED` awards (BE-PRZ-02)
- user read-only prize award history, user claim submission, and manager award/claim APIs

Current prizes API:

- `/api/prizes/my-awards/`
  - `GET`: authenticated user lists their own `PrizeAward` rows across `Customer` and `Seller` modes
- `/api/prizes/my-awards/<id>/`
  - `GET`: authenticated user retrieves one of their own `PrizeAward` rows (`404` for another user's award)
- `/api/prizes/my-awards/<id>/claim/`
  - `POST`: authenticated owner submits a claim (`full_name`, `address`, `identity_number`) for a `PENDING` `CLAIM_REQUIRED` `PrizeAward`; returns `id`, `award_id`, `status`, and `submitted_at` without echoing PII
- `/api/prizes/admin/awards/`
  - `GET`: manager lists all `PrizeAward` rows with `prizes.awards.list`; supports `status`, `fulfillment_type`, `source_type`, and `user` filters
- `/api/prizes/admin/awards/<id>/`
  - `GET`: manager retrieves one `PrizeAward` row with `prizes.awards.retrieve`
- `/api/prizes/admin/claims/`
  - `GET`: manager lists submitted `PrizeClaim` rows with `prizes.claims.list`; supports `status` and `source_type` (`award__source_type`) filters
- `/api/prizes/admin/claims/<id>/`
  - `GET`: manager retrieves one `PrizeClaim` row (including `full_name`, `address`, `identity_number`, and linked award details) with `prizes.claims.retrieve`
- `/api/prizes/admin/claims/<id>/review/`
  - `POST`: manager marks a `SUBMITTED` claim as `REVIEWED` (`prizes.claims.review`); idempotent on repeat review
- `/api/prizes/admin/claims/<id>/deliver/`
  - `POST`: manager marks a `REVIEWED` claim as `DELIVERED` and atomically fulfills the linked `PrizeAward` (`prizes.claims.deliver`); idempotent on repeat deliver

Current prizes behavior:

- `PrizeAward` is owned by `UserAccount` (`user`), with optional `profile` as context only (`profile.user_id == user_id` enforced); switching between `Customer` and `Seller` modes does not hide or duplicate awards
- `PrizeAwardSourceType` includes `SEASON`, `REFERRAL_CHALLENGE`, and `LUCKY_WHEEL` (`WEEKLY_DRAW` is excluded because the weekly onboarding draw is cancelled per `BE-DRW-CANCEL-01`)
- `DIGITAL_AUTOMATIC` awards (`PrizeAwardService.record_digital_fulfilled`) are always created with `status=FULFILLED` and `fulfilled_at` set, after the source service has already executed the digital reward (`PointService`, `UserPromotionGrantService`, or `BadgeService`); `PrizeAwardService` never re-executes point, promotion, or badge grants itself and creates `0` generic `prize_awarded` notifications so digital PP Coin / badge rewards do not emit duplicate notifications
- `CLAIM_REQUIRED` awards (`PrizeAwardService.record_claim_required`) are created with `status=PENDING` and `fulfilled_at=None` for canonical non-digital in-kind prizes (`ReferralChallengePrizeCode.IPHONE`, `ReferralChallengePrizeCode.AIRPODS`) and atomically create one `prize_awarded` congratulatory `Notification` (`idempotency_key=f"prize_awarded:{award.id}"`, BE-NOT-01) when newly created (`created_new=True`)
- `PrizeAward` rows are immutable after creation (except `CLAIM_REQUIRED` `status=PENDING -> FULFILLED` and `fulfilled_at` when their linked `PrizeClaim` is marked `DELIVERED`) and cannot be deleted (`ProtectedError`)
- `PrizeAward` stores zero claimant PII (`FORBIDDEN_PRIZE_PII_KEYS` such as `name`, `address`, `shipping_address`, `national_id`, and `identity_number` are rejected in `prize_snapshot` and `metadata`)
- `PrizeClaim` (`OneToOneField` to `PrizeAward`, `on_delete=PROTECT`) stores `full_name`, `address`, `identity_number`, `status` (`SUBMITTED`, `REVIEWED`, `DELIVERED`), `submitted_at`, `reviewed_at`/`reviewed_by`, and `delivered_at`/`delivered_by`; claimant fields are immutable after submission and `delete()` raises `ProtectedError`
- `PrizeClaimService.submit_claim()` locks the `PrizeAward` row, requires `award.user_id == user.id`, `fulfillment_type=CLAIM_REQUIRED`, `status=PENDING`, and no existing claim, and creates a `SUBMITTED` claim while keeping `PrizeAward.status=PENDING` (`fulfilled_at=None`)
- `PrizeClaimService.review_claim()` transitions `SUBMITTED -> REVIEWED` (idempotent if already `REVIEWED`) while keeping `PrizeAward.status=PENDING`
- `PrizeClaimService.deliver_claim()` requires `claim.status == REVIEWED` (rejecting `SUBMITTED -> DELIVERED` with `400`), transitions `REVIEWED -> DELIVERED`, and atomically sets `PrizeAward.status = FULFILLED` and `PrizeAward.fulfilled_at = delivered_at` (idempotent if already `DELIVERED`)
- public `MyPrizeAwardSerializer` exposes `id`, `source_type`, `source_id`, `fulfillment_type`, `status`, `title`, `description`, `prize_snapshot`, `awarded_at`, `fulfilled_at`, safe claim summary fields (`claim_status`, `claim_submitted_at`, `claim_reviewed_at`, `claim_delivered_at`), and `created`, hiding `idempotency_key`, internal `metadata`, and claimant PII (`full_name`, `address`, `identity_number`)


### `apps.general`

Current responsibilities:

- base models
- permissions / authorization helpers
- shared constants / helpers
- admin announcement bar (`AdminAnnouncement`) and per-`UserAccount` dismissal tracking (`AnnouncementDismissal`) (BE-ANN-01)
- single-row manager-controlled client feature flags (`AppSettings`), currently the referral share link visibility switch and window

Current announcements API:

- `/api/announcements/current/`
  - `GET`: authenticated user lists currently active (`is_active=True`, `starts_at <= now < expires_at`, `deleted__isnull=True`) announcements not dismissed by `request.user`; each row exposes `show_in_bar`, and `?show_in_bar=true|false` filters by it (the announcement bar uses `?show_in_bar=true`)
- `/api/announcements/<id>/dismiss/`
  - `POST`: authenticated user records an idempotent dismissal for `request.user` (`UserAccount`); survives `Customer` <-> `Seller` profile switching and rejects client-supplied `user_id`/`profile_id`
- `/api/announcements/admin/`
  - `GET` / `POST`: manager list/create announcements with `announcements.list` or `announcements.create`
- `/api/announcements/admin/<id>/`
  - `GET` / `PATCH` / `DELETE`: manager retrieve/update/soft-delete an announcement with `announcements.retrieve`, `announcements.update`, or `announcements.delete`

Current announcements behavior:

- `AdminAnnouncement` stores `text`, `starts_at`, `expires_at`, and `is_active`; admin create/update accepts either `expires_at` directly or `duration_value` + `duration_unit` (`hours` / `days`) and persists `expires_at`
- expired announcements disappear automatically via `starts_at <= now < expires_at` queryset filtering without a Celery task
- `AnnouncementDismissal` enforces `unique(announcement, user)` on `UserAccount`; republishing is represented as a new `AdminAnnouncement` row so prior dismissals do not hide republished announcements
- announcement operations do not create `Notification` rows or trigger push/WebSocket tasks
- `AdminAnnouncement.show_in_bar` (default `true`, admin-writable on create/`PATCH`, admin-filterable) is an extra placement switch on top of `is_active`: an active announcement with `show_in_bar=false` is still returned by `/api/announcements/current/` but is not meant for the announcement bar; `is_active=false` still hides an announcement everywhere

Current app settings API:

- `/api/app-settings/`
  - `GET`: authenticated customer/seller (or manager with `app_settings.retrieve`) reads client feature flags: `{"referral_share_visible": <bool>}`
- `/api/app-settings/admin/`
  - `GET`: manager with `app_settings.retrieve` reads `referral_share_enabled`, `referral_share_starts_at`, `referral_share_ends_at`, computed `referral_share_visible`, and `modified`
  - `PATCH`: manager with `app_settings.update` changes any of `referral_share_enabled`, `referral_share_starts_at`, `referral_share_ends_at` (`null` clears a bound)

Current app settings behavior:

- `AppSettings` is one row (`id=1`, enforced by a DB check constraint) created lazily with defaults by `AppSettingsService.get()`; it has no create/delete API and no soft delete
- `referral_share_visible` is `true` only when `referral_share_enabled` is `true` and `now` is inside the optional half-open `[referral_share_starts_at, referral_share_ends_at)` window; a null bound is open-ended, so hiding the link for a period means setting `referral_share_starts_at` to when it should come back (or `referral_share_ends_at` to when it should disappear)
- `referral_share_starts_at < referral_share_ends_at` is validated by `AdminAppSettingsSerializer` (against stored values on partial updates) and a DB check constraint
- the flag is display-only for the frontend: it does not block `POST /api/referrals/codes/`, code redemption, referral approval, or referral challenge scoring


### Shared Abstractions

- `apps.general.models.BaseModel`: timestamped + soft-delete base model
- `apps.general.models.OwnerBaseModel`: common owner relation to `users.Profile`
- `apps.general.models.ResourcePermission`: persisted resource/owner placeholder; current `HasAppAccess` does not consult this model
- `apps.general.authorizations.HasAppAccess`: repo-specific permission gate used by app viewsets
- `apps.users.services.ManagerPermissionService`: central manager permission normalizer/checker with exact action-code matching and superuser bypass
- manager-enabled views declare exact manager permissions with `manager_permission_map`, `manager_permission_namespace`, or `get_required_manager_permission()`; HTTP methods alone do not grant manager access
- `HasAppAccess.allow_scoped_read_access`: opt-in read behavior for resources whose `get_queryset()` already scopes readable objects
- `HasAppAccess.permission_owner_field`: optional view attribute for object checks that use a non-`owner_id` ownership field, such as `OrderItem.seller_id`
- `apps.general.viewsets.CreateListViewSet`: shared DRF base for endpoints that only need `create` and `list`

### Main Domain Concepts

#### UserAccount

Authentication identity. A user may have multiple profiles. The active profile determines current behavior and permission context.

Optional account fields:

- `secondary_phone_number`: optional, non-unique secondary contact number; not used for login or `USERNAME_FIELD`

#### Profile

Profile is the real business actor in the system.

Optional shared profile fields:

- `loft_name`: optional loft display name
- `loft_picture`: optional loft image uploaded through existing profile update flows

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
- do not add `active_profile` presence checks such as `if not active_profile`, `getattr(user, "active_profile", None)`, or `hasattr(user, "active_profile")`
- do not duplicate `active_profile` validity checks for `is_active`, `is_valid`, `activated`, or `deleted` after retrieving `user.active_profile`
- rely on `user.active_profile` to raise when the authenticated user has no current valid active profile
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

#### Package

Package is a manager-created seller plan/template.

Current design choice:

- `Package` inherits `BaseModel`, not `OwnerBaseModel`
- packages define available points, price, activation duration, auction cost, product cost, and availability
- sellers can list only available packages
- managers can create and update packages

#### SellerPackage

SellerPackage is the seller-owned package instance and request record.

Current seller package statuses:

- `pending`
- `active`
- `exhausted`
- `cancelled`

Current design choice:

- `SellerPackage.owner` is a concrete `SellerProfile`
- seller requests create `AssetSellerPackage` rows by default
- `AssetSellerPackage` and `PedigreeSellerPackage` are thin concrete subclasses
- multiple active activated seller packages are allowed per seller
- `SellerPackage` carries room-like public metadata through `display_name`, `description`, and `is_public`
- activation and cancellation happen through service methods, not direct serializer field updates
- package point consumption is connected to seller-created auctions and seller-created product assets
- package point consumption is not connected to pedigrees yet
- manager-created platform supplies bypass package point consumption

#### PackageUsageLog

PackageUsageLog records successful package point consumption.

Current design choice:

- usage log owner is a concrete `SellerProfile`
- usage logs point to `SellerPackage`
- related objects are stored as optional `related_object_type` plus `related_object_id`
- usage logs are written for seller auction and product creation
- usage logs are not written by pedigree workflows yet

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
- `Order.source` is a checkout-time attribution snapshot (`mobile`, `web`, `unknown`) and is never edited afterwards
- there is no seller field on `Order`

#### OrderItem

OrderItem is the real market business unit.

Each item has:

- direct `Asset` reference
- explicit `SellerProfile` reference
- quantity
- unit price snapshot
- gross total
- optional discount offer and discount amount
- optional cashback offer, redeemed amount, and earned amount
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

Manual market sale completion operates at the order-item level through `MarketPaymentRequest`.

#### DiscountOffer

DiscountOffer is an admin-created marketplace promotion that reduces an order item's price at creation time.

Current design choice:

- offers are immutable after creation except activation state
- offers target all assets, one category, or one asset
- offer selection is automatic and service-owned, not frontend-driven
- discounts are stored on `OrderItem` as the selected offer and calculated discount amount
- sellers cannot create or manage discount offers

#### CashbackOffer

CashbackOffer is an admin-created marketplace promotion that defines automatic redemption and earning rules.

Current design choice:

- offers are immutable after creation except activation state
- offers target all assets, one category, or one asset
- redemption happens at seller order-item approval, not order creation
- earning happens at market sale completion, not seller order-item approval
- auction cashback is not implemented
- sellers cannot create or manage cashback offers

#### CashbackTransaction

CashbackTransaction is the ledger for customer cashback balance movement.

Current design choice:

- each transaction belongs to a concrete `CustomerProfile`
- every transaction points to a `CashbackOffer`; admin adjustments use the system `Manual Admin Adjustment` offer
- transaction types are `earned`, `redeemed`, `refunded`, and `admin_adjustment`
- `idempotency_key` is required and unique
- non-admin transaction amounts are positive; admin adjustment amounts may be positive or negative
- `balance_after` must never be negative
- balance changes must happen through `CashbackService`, not direct model updates

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
- customer profiles can soft-delete their own comments; managers can moderate-delete comments with `comments.delete`
- comment throttling is validation-layer only, not a database constraint
- comments are flat; replies/threading and reporting are not implemented

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
- manager rating deletion soft-deletes the rating, hides any inline rating comment, and refreshes cached aggregates

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
- result writes must use the service layer so race result ranks stay consistent

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
- sold auction items do not auto-create payment records; the winner manually creates an `AuctionPaymentRequest`

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
- API localization is Arabic-first:
  - `LANGUAGE_CODE = "ar"`
  - supported languages are `ar` and `en`
  - `LOCALE_PATHS` points to the repo-level `locale/` directory
  - `apps.general.middleware.ApiLocaleMiddleware` selects language from `?lang=`, then `Accept-Language`, then Arabic default
  - locale values are normalized to `ar` or `en`; unsupported languages fall back to Arabic

Localization rules:

- translate only human-readable API messages, labels, descriptions, validation errors, permission-denied/authentication text, response `detail`/`message` text, notification title/body display, and display labels
- do not translate API field names, URLs, enum/status values stored in the database, notification type values, source type values, profile type values, permission codes, group keys, or other machine/database codes
- use Django gettext helpers, usually `gettext_lazy as _` for model/constant/class-level strings and `gettext as _` for direct runtime response text that may render after view return
- manager permission catalog entries keep `code` and `group_key` as stable English machine strings; only `group_label`, `label`, and `description` are localized
- tests that assert English text must explicitly request English with `Accept-Language: en` or use a translation override; no-header API requests should be treated as Arabic

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
- `/api/deal-ratings/`
- `/api/races/`
- `/api/race-results/`
- `/api/packages/`
- `/api/seller-packages/`
- `/api/notifications/`
- `/api/payments/`
- `/api/complaints/`
- `/api/promotions/`
- `/api/chat/`
- `/api/feed/`
- `/api/insights/`
- `/api/pedigrees/`
- `/api/prizes/`
- `/api/announcements/`
- `/api/app-settings/`

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
- post-auction sale completion starts only when the buyer creates an `AuctionPaymentRequest`

### Payments

There is no gateway payment integration for buyer-seller marketplace sales.

Current approach:

- PPW does not process real money for buyer-seller marketplace sales
- no payment gateways, Apple Pay, Google Pay, card processing, escrow, or wallet behavior exists for marketplace sales
- payment happens manually outside the app between buyer and seller
- buyer creates a manual `PaymentRequest` for a won auction item or approved market order item
- seller approves or rejects the request manually
- seller approval finalizes the sale inside the system
- auction flow is: customer wins auction item, customer creates `AuctionPaymentRequest`, seller approves or rejects, seller approval finalizes the internal sale, bird ownership transfers, and paired birds transfer when applicable
- market flow is: customer creates an order/order item, seller approves the `OrderItem`, customer creates `MarketPaymentRequest`, seller approves or rejects, seller approval finalizes the internal sale, product stock is reduced, and non-bird product ownership does not transfer
- normal payment flow has no automated settlement, capture, payment gateway, admin approval, payment proof upload, escrow, refund automation, shipping confirmation, buyer receipt confirmation, or handoff model

### Promotions / Cashback

Current approach:

- discounts are admin-created and apply automatically to marketplace order items at order creation
- cashback offers are admin-created and apply automatically to marketplace order items at seller approval and sale completion
- cashback redemption deducts from `CustomerProfile.cashback_balance` when a seller approves an order item
- cashback earning credits `CustomerProfile.cashback_balance` only after the related market `PaymentRequest` is approved
- every cashback balance change must have a `CashbackTransaction` with an idempotency key
- cashback is an internal customer app balance only, not withdrawable cash and not an external wallet
- cashback is not connected to payment capture, refunds, or shipping
- `DiscountOffer` and `CashbackOffer` are admin-managed and immutable after creation except activation/deactivation

### Handoff / Delivery

There is no shipping integration.

Current approach:

- no handoff or delivery model exists
- no delivery tracking exists
- no buyer receipt confirmation exists
- no logistics provider integration exists
- seller approval of a `PaymentRequest` is the MVP completion confirmation
- do not implement shipping, handoff, delivery tracking, or buyer receipt confirmation as part of payment work unless the task explicitly requests that product-policy change

### Notifications

Current approach:

- `Notification` database rows are now the source of truth for in-app notifications
- polling APIs exist for notification list, unread count, mark read, and mark all read
- current in-app notifications are DB-backed only
- do not treat `OrderEvent`, `PaymentRequestEvent`, `ComplaintEvent`, or `AuctionEvent` rows as notification records
- no device tokens, FCM/APNs, push delivery, WebSocket, SSE, email, SMS, WhatsApp, notification preferences, or Do Not Disturb are implemented

Agreed future direction beyond the current MVP:

- add device tokens later
- add FCM/APNs later for closed-phone push notifications
- add WebSocket or SSE only if polling is no longer enough for the product requirement

### Ownership

Current short-term approach:

- only current owner is stored now
- full ownership history is intentionally deferred
- ownership transfer must happen only via payment service approval logic
- seller-approved payment requests transfer ownership only for bird assets
- non-bird product assets keep seller ownership even after market stock is reduced
- do not mutate ownership casually from serializers or views

### QR / Bird Identity

Current approach:

- `Bird.public_id` is the stable public UUID identity for public bird pages and QR payload URLs
- public bird lookup must use `public_id`, not internal `Bird.id`
- internal database ids must not be used as public QR identity
- the backend exposes public-safe bird data by `public_id`
- the backend returns `qr_payload_url` using `PUBLIC_FRONTEND_BASE_URL`
- the backend does not store QR image files and does not generate QR images in the current MVP
- frontend clients render QR codes from `qr_payload_url`

### Assets / Media

Current assets app is domain modeling plus simple URL/reference-based asset media records, not full file/media handling or verification.

Current e-commerce foundation:

- assets can now carry shared price and listing fields
- product assets now have CRUD endpoints for supplies, accessories, feeds, and supplements
- product asset list endpoints support filtering, search, and ordering
- carts, checkout, orders, and order items now have an API / serializer / validator / service layer
- order aggregate list endpoints support exact `status` filtering
- customers can add listed seller assets to a persistent server-side cart
- customers can checkout the full active cart into a pending seller decision market order
- direct `POST /api/orders/` remains available as a compatibility alias with the same stock-validation semantics
- active discount offers apply automatically to order items at checkout/direct order creation
- active cashback offers apply automatically at seller order-item approval and market sale completion
- sellers can approve or reject their own order items
- checkout-time stock validation does not decrement stock or change listing state
- seller order-item approval does not update stock or listing state
- seller approval of a market `PaymentRequest` updates stock and can mark zero-count assets unavailable
- seller rejection updates item/order statuses but does not change stock
- lightweight order events exist as notification placeholders
- manual PaymentRequest records complete approved market order items

Current media implementation:

- `Media`
- `AssetMedia`
- `media_url`
- `media_type` values `image` and `video`
- primary/order metadata through `is_primary` and `order`

Not currently implemented:

- upload processing
- S3 pipeline orchestration
- FFmpeg
- watermarking
- anti-fraud code
- mandatory controlled in-app video capture
- minimum four-image validation
- MIME scanning

Current product asset visibility:

- managers can list, retrieve, create, update, and delete platform supplies only with `platform_products.*` permissions
- managers can list, retrieve, create, update, and delete non-supply product assets only where explicit `assets.*` manager mappings remain declared
- platform supplies are not owned by manager profiles; they are owned by the hidden platform seller profile
- sellers can create, update, and delete only their own accessory, feed, and supplement assets
- sellers can read their own product assets plus market-listed product assets
- customers can read only market-listed product assets
- product safe-read visibility is scoped before django-filter filters and ordering run

### Seller Packages

- package plans are created and updated by managers
- sellers can request available package plans
- package activation is manual and manager-driven
- a seller can have multiple active activated packages at a time
- manager activation of a new package does not deactivate the seller's previous active packages
- pending package requests can be cancelled by managers
- active packages cannot be cancelled directly in the current API
- selected active `SellerPackage` points are consumed by seller auction creation and seller product asset creation
- package points are not consumed by manager-created auctions/products, bird creation, product edits, auction updates, auction cancellation, auction close, bidding, buy-now, or pedigree workflows
- package consumption creates `PackageUsageLog` records for successful seller auction/product creation only

Planned direction:

- fuller media handling around existing asset image references
- bird video
- pedigree attachments
- race result attachments

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
- create payment request
- approve payment request
- reject payment request
- complete market sale from payment request

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
- seller order-item approval/rejection
- payment request creation/update
- seller payment request approval/rejection
- market stock reduction during payment request approval
- bird ownership transfer during payment request approval
- auction settlement

### Idempotent Critical Actions

Actions like these must be protected from duplicate execution:

- create payment request
- approve payment request
- reject payment request
- transfer bird ownership during payment request approval

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
- `DISCOUNT_OFFER`
- `CASHBACK_OFFER`
- `CASHBACK`
- `DISCOUNT_OFFER_ADMIN`
- `CASHBACK_OFFER_ADMIN`
- `CASHBACK_TRANSACTION_ADMIN`
- `CASHBACK_ADJUSTMENT_ADMIN`
- `LUCKY_WHEEL_PRIZE_ADMIN`
- `LUCKY_WHEEL_SPIN_ADMIN`
- `SEASONS_ADMIN`
- `PEDIGREE_DOCUMENT`
- `REGISTERED_PEDIGREE`
- `FEED_BLOCK`
- `REWARD_RULE_ADMIN`
- `DEAL_RATING`
- `REFERRAL_ADMIN`
- `REFERRAL_CHALLENGE_ADMIN`
- `PRIZE_AWARD_ADMIN`
- `ANNOUNCEMENT`
- `ANNOUNCEMENT_ADMIN`
- `APP_SETTINGS`
- `APP_SETTINGS_ADMIN`

Permission implementation rule:

- all custom DRF permission classes must live in `apps.general.authorizations`
- app-local custom permission classes are not allowed
- do not introduce any new permission class without explicit user approval
- do not introduce a new authorization approach unless the user explicitly asks for it, or after asking under necessary conditions
- new endpoint authorization must use `apps.general.authorizations.HasAppAccess` plus `CUSTOMER_PERMISSIONS` / `SELLER_PERMISSIONS` unless the user explicitly approves another approach
- viewsets using `HasAppAccess` must define both `queryset` and `permission_resource`
- keep profile-specific business validation in serializers, validators, or services when `HasAppAccess` is only the endpoint gate

Current product asset permission pattern:

- use `HasAppAccess`
- set `permission_resource = "PRODUCT_ASSET"`
- set `allow_scoped_read_access = True`
- scope readable objects in `get_queryset()` before filters run
- `SupplyViewSet` overrides manager permission resolution with `manager_permission_namespace = "platform_products"`
- `AccessoryViewSet`, `FeedViewSet`, and `SupplementViewSet` keep generic product asset behavior

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
- managers need `orders.<level>` permissions to pass `HasAppAccess` for cart and checkout, but service validation still requires a customer profile for cart/checkout execution
- dynamic per-user resource permissions are planned after most resources are implemented
- authenticated users with no active valid role are denied by `HasAppAccess` before resource action checks run
- sellers must switch to customer mode before using cart or checkout endpoints
- sellers do not access `/api/orders/`; seller order work happens through order-item endpoints
- `OrderItemViewSet` uses `HasAppAccess` with `permission_resource = "ORDER_ITEM"`
- order-item object checks use `permission_owner_field = "seller_id"`
- sellers can list, retrieve, approve, and reject only their own seller-side order items
- managers with `orders.<level>` permissions can access order and order-item endpoints, subject to the same view/service constraints as before

Current ratings/comment permission pattern:

- `AssetCommentViewSet` uses `HasAppAccess` with `permission_resource = "ASSET_COMMENT"`
- `SellerCommentViewSet` uses `HasAppAccess` with `permission_resource = "SELLER_COMMENT"`
- `AssetRatingViewSet` uses `HasAppAccess` with `permission_resource = "ASSET_RATING"`
- `SellerRatingViewSet` uses `HasAppAccess` with `permission_resource = "SELLER_RATING"`
- customers and sellers can list and create comments through shared permission constants
- customers can destroy their own comments through shared permission constants and object ownership checks
- customers can list and create asset/seller ratings through shared permission constants
- sellers can list asset/seller ratings but cannot create ratings
- rating creation remains customer-only through service validation after the shared endpoint permission gate
- manager comment detail/delete uses `comments.retrieve` and `comments.delete`; manager rating detail/delete uses `ratings.retrieve` and `ratings.delete`
- ratings and comments must not use app-local permission classes

Current race permission pattern:

- `RaceViewSet` uses `HasAppAccess` with `permission_resource = "RACE"`
- `RaceResultViewSet` uses `HasAppAccess` with `permission_resource = "RACE_RESULT"`
- `RaceResultListCreateViewSet` uses `HasAppAccess` with `permission_resource = "RACE_RESULT"`
- race and race-result viewsets set `allow_scoped_read_access = True` because readable race data is intentionally available to any authenticated role
- managers with `races.<level>` permissions can create, update, delete, and bulk-create race data
- sellers and customers can list/retrieve races, list per-race results, and search race results only
- race write validation and rank recalculation live in serializers/services, not in permission classes

Current package permission pattern:

- `PackageViewSet` uses `HasAppAccess` with `permission_resource = "PACKAGE"`
- sellers can list packages, but queryset scoping limits them to `is_available=True`
- managers with `packages.<level>` permissions can list all packages and create/update package plans
- `MySellerPackageViewSet` uses `HasAppAccess` with `permission_resource = "MY_SELLER_PACKAGE"`
- sellers can list and create only their own seller package requests
- `ManagerSellerPackageViewSet` uses `HasAppAccess` with `permission_resource = "SELLER_PACKAGE"`
- manager seller-package updates are PATCH status transitions only, not arbitrary field updates
- customers have no package permissions in `CUSTOMER_PERMISSIONS`

Current promotions permission pattern:

- current offer list endpoints use `HasAppAccess` with `DISCOUNT_OFFER` and `CASHBACK_OFFER`
- customer cashback balance/history endpoints use `HasAppAccess` with `CASHBACK`
- admin offer management endpoints use `DISCOUNT_OFFER_ADMIN` and `CASHBACK_OFFER_ADMIN`
- admin transaction and adjustment endpoints use `CASHBACK_TRANSACTION_ADMIN` and `CASHBACK_ADJUSTMENT_ADMIN`
- customers can list current offers and their own cashback balance/transactions
- managers can create, activate, deactivate, and list offers, list all cashback transactions, retrieve customer cashback balances with `cashback.balance.retrieve`, and perform cashback adjustments
- managers can adjust normal customer/seller profile balances only through `/api/users/admin/profile-balance-adjustments/` with `profile_balances.adjust`
- sellers have no direct promotions permissions in this sprint
- promotion services still enforce concrete customer/manager profile requirements where endpoint-level permission is not enough

Current reward admin permission pattern:

- Lucky Wheel prize admin endpoints use `HasAppAccess` with `LUCKY_WHEEL_PRIZE_ADMIN`
- Lucky Wheel spin admin listing uses `HasAppAccess` with `LUCKY_WHEEL_SPIN_ADMIN`
- seasons endpoints use `HasAppAccess` with `SEASONS_ADMIN`
- reward rule admin endpoints use `HasAppAccess` with `REWARD_RULE_ADMIN`
- referral admin endpoints use `HasAppAccess` with `REFERRAL_ADMIN`; referral challenge admin endpoints use `REFERRAL_CHALLENGE_ADMIN`
- prize award admin endpoints use `HasAppAccess` with `PRIZE_AWARD_ADMIN`
- required manager codes are exact action strings: `lucky_wheel.prizes.list`, `lucky_wheel.prizes.create`, `lucky_wheel.prizes.update`, `lucky_wheel.spins.list`, `seasons.list`, `seasons.create`, `seasons.update`, `seasons.calculate`, `seasons.finalize`, `seasons.insights.list`, `seasons.prizes.list`, `seasons.prizes.create`, `seasons.prizes.update`, `seasons.prizes.delete`, `reward_rules.list`, `reward_rules.retrieve`, `reward_rules.update`, `referrals.list`, `referrals.approve`, `referrals.reject`, `referral_challenges.list`, `referral_challenges.retrieve`, `referral_challenges.create`, `referral_challenges.update`, `referral_challenges.finalize`, `prizes.awards.list`, and `prizes.awards.retrieve`
- user-triggered Lucky Wheel spin, referral redeem, cashback-to-points conversion, and `my-awards` read endpoints do not require manager permission codes

Current pedigree permission pattern:

- `PedigreeDocumentViewSet` and `PedigreeDocumentReviewViewSet` use `HasAppAccess` with `permission_resource = "PEDIGREE_DOCUMENT"`
- customers and sellers can create, retrieve, and review only their own pedigree documents
- managers can retrieve pedigree documents with `pedigrees.documents.retrieve` and review documents with `pedigrees.documents.review`; upload/create remains a profile-owned customer/seller flow
- retrieve uses scoped read access only after `get_queryset()` limits non-manager reads to the active profile owner
- review relies on profile ownership through `owner_id` and service-level actor validation
- `PedigreeBirdViewSet` uses `HasAppAccess` with `permission_resource = "PEDIGREE_BIRD"` and `permission_owner_field = "registered_by_id"`, so customer/seller object checks follow the registration scope and unowned ancestors stay editable by their registrant
- `PedigreeBirdOwnershipViewSet` (claim/release) uses `HasAppAccess` with `permission_resource = "PEDIGREE_BIRD"` mapped to `create` and declares no manager permission, so managers are denied by default and change owners through `PATCH` instead
- `RegisteredPedigreeViewSet` uses `HasAppAccess` with `permission_resource = "REGISTERED_PEDIGREE"` and `allow_scoped_read_access = True`; customers and sellers have `list` and `retrieve` only, scoped by `RegisteredPedigreeService.visible_queryset()`
- managers use exact `pedigrees.registered.list`, `pedigrees.registered.retrieve`, `pedigrees.registered.create`, `pedigrees.registered.update`, and `pedigrees.registered.delete` permissions

Current auction permission pattern:

- `AuctionViewSet` uses `IsAuthenticated` plus `HasAppAccess` with `permission_resource = "AUCTION"`
- sellers can create and manage their own auctions through owner-scoped querysets for owner actions
- managers can list, retrieve, cancel, and close auctions only through exact `auctions.list`, `auctions.retrieve`, `auctions.cancel`, and `auctions.close` permissions
- managers are not currently authorized to create, update, or delete auctions through manager permissions
- customers can use configured auction read actions, but `AuctionViewSet` does not set `allow_scoped_read_access`
- `AuctionItemViewSet`, `BidViewSet`, and `AuctionEventViewSet` use DRF read-only public permissions for list/retrieve and custom auth checks on write actions
- `place_bid` and `buy_now` require authentication and reject manager profiles at the permission layer before business validation

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
- simple URL/reference-based asset media records through `Media` and `AssetMedia`
- bird entity, CRUD endpoint, and validation
- product asset CRUD for supplies, accessories, feeds, and supplements
- product asset filtering, search, and ordering
- seller package point consumption for seller-created auctions and product assets
- persistent customer cart and cart item API foundation for market flows
- checkout and compatibility direct-order APIs for fixed-price order creation
- order validators and services for stock-validating fixed-price order creation
- order-item discount snapshots through admin-created discount offers
- marketplace cashback balance, offer, transaction, redemption, earning, refund-helper, and admin-adjustment services
- loyalty reward engine, temporary badge validity, reward idempotency, and trusted buyer/seller aggregate task entry points
- DB-backed notification table plus polling read/unread APIs
- seller item approval/rejection foundation
- seller-decision timeout auto-cancellation for stale pending market order items
- manual PaymentRequest flow for won auction items and approved marketplace order items
- market stock reduction during seller-approved market PaymentRequests
- bird ownership transfer during seller-approved PaymentRequests
- split asset/seller comments and customer asset/seller ratings as a simple trust layer
- nested rating comment creation with `is_rating_comment=True`
- cached rating aggregates on assets and seller profiles
- lightweight order, payment-request, complaint, and auction event rows alongside the new notification table
- auctions, auction items, bids, and events
- auction creation, bidding, limited update locking, cancellation, buy-now, and close/winner-resolution flows
- service / validator / factory-style pattern in auctions, although the view still constructs the unsaved `Auction` before delegating persistence to `AuctionService.create_auction()`
- Dockerized local and production runtime configuration
- Redis + Celery worker/beat infrastructure for background jobs
- automatic scheduled auction closing through Celery Beat
- safe in-app notification hooks for chat messages, payment requests, checkout seller-pending items, seller order-item decisions, seller decision timeouts, auction bids, auction wins, buy-now success, complaint status updates, manual manager notifications, and badge awards
- seller follow relationships for customer profiles
- cursor-based auction and market home feeds that rank followed seller/package content before deterministic discovery fallback
- pedigree document upload, null OCR extraction, retrieval, and manual review APIs

Current API documentation coverage:

- mounted DRF routers, APIViews, and manual `GenericViewSet.as_view(...)` endpoints appear in the schema
- custom actions in auctions, auth, manual cart/checkout ViewSets, and order-item approval/rejection have targeted schema annotations
- schema tests cover auth policy for `/api/schema/`, Bearer security output, cart/checkout paths, and representative custom/manual endpoints

The codebase does not yet fully implement:

- media/file upload, storage, processing, or moderation subsystem beyond simple URL/reference-based asset media records
- payment proof upload or admin payment review
- handoff or shipping workflow
- ownership history workflow
- expanded dispute flow beyond the current simple complaint records
- rating/comment moderation, replies, reports, likes, notifications, or analytics
- full pedigree system beyond the current document OCR extraction MVP
- pedigree reader
- expanded QR generation, printing, and transfer flows beyond the current public identity URL
- richer rewards programs beyond the current MVP reward engine
- promotion features beyond the current marketplace discount/cashback MVP, such as expiration, coupons, seller-created offers, auction cashback, usage caps, and complex campaign limits
- realtime or expanded chat beyond the current REST-only direct chat MVP
- push or external notification delivery beyond the current DB-backed in-app notification MVP
- custom pedigree design, cloud backup, medical-record, and QR-generation workflows beyond subscription entitlement codes

Important framing:

Current repo is a strong auction-oriented foundation, not a full product-complete backend yet.

Do not assume planned concepts already exist. Planned concepts must be labeled as planned, upcoming, or not yet implemented until code is present.

## Known Gaps / Known Risks

These risks are important and should be respected during future work.

### Auth / Permission Risks

- profile switching must respect activation and business constraints
- token `profile` claim and active profile behavior must stay aligned
- test JWTs must include `profile` unless testing missing-claim behavior
- manager permission-resource mapping must stay synchronized with `HasAppAccess.permission_resource` usage across apps
- manager permission checks are module-level only in this MVP; there is still no object-level manager permission system
- `allow_scoped_read_access` can expose data if used without correct `get_queryset()` scoping
- `BirdViewSet` list visibility is not scoped even though object checks are owner-based
- `AuctionViewSet` visible list behavior and object-level retrieve behavior are not fully aligned because scoped safe reads are not enabled there

### Auction Risks

- bid validation must be correct and re-checked under DB lock
- event types must be consistent
- auction editing restrictions are partially enforced for `PATCH`, but default destroy still uses the base `ModelViewSet` path
- birds must not be reusable in conflicting active auctions
- seller-created auction validation now verifies submitted bird ownership; manager-created auctions still bypass that seller ownership rule through the administrative path
- regular bids require a customer profile, but buy-now currently lacks the same customer-profile type check
- same-user customer/seller profile bidding against that user's own seller auction is not explicitly blocked
- `Auction.is_active()` currently checks time only and does not include `Auction.status`
- manual `close_auction()` currently does not enforce `end_time <= now`; only the scheduled selector filters expired auctions
- `cancel_auction()` blocks `ended` and already-cancelled auctions, but does not explicitly block `closed`
- `AuctionFactory.create_auction()` is currently stale/unused and references non-model `auto_extend_threshold`
- permission constants include auction actions such as `publish` and `ended`, but no matching view actions currently exist
- settlement must enforce end-time and state rules before post-auction completion is added

### Package Consumption Risks

- manager-created auctions and products intentionally bypass seller package consumption; preserve this unless product policy changes
- package consumption is enforced through the selected `SellerPackage`; current seller requests still create `AssetSellerPackage` rows by default
- auction and product asset rows store nullable `seller_package` FKs for historical traceability
- `PackageService.validate_can_consume()` is a preflight check only; final point decrement must still call `PackageService.consume_points()` inside the object creation transaction
- SQLite does not enforce `select_for_update()` row locks, so true package consumption concurrency behavior must be verified on PostgreSQL

### Promotions Risks

- cashback balance changes rely on row locks; true concurrent redemption behavior must be verified on PostgreSQL, not SQLite
- never update `CustomerProfile.cashback_balance` directly; use `CashbackService` so a `CashbackTransaction` is written
- promotion offers are immutable after creation except activation state; do not add offer update endpoints without a product decision
- current promotions do not implement expiration, coupons, seller-created offers, auction cashback, per-user usage caps, admin approval per transaction, or withdrawal behavior

### Business Flow Gap

Current completion MVP:

- buyer-created `PaymentRequest` records complete won auction items and approved market order items after seller approval
- loyalty sale-completion rewards are granted only from `CompletedDeal` (`DEAL_COMPLETED`), which is recorded after seller approval of a `PaymentRequest` and the relevant auction or market finalization succeed
- related buyers and sellers can create simple complaints for approved or rejected payment requests; managers resolve or reject them manually

Important remaining gaps:

- no payment proof upload or admin payment review exists
- no handoff, shipping, or logistics workflow exists
- no ownership history table exists
- no automatic refund, ownership rollback, cashback reversal, complaint chat, complaint attachments, or seller penalty workflow exists

### Media Gap

Current assets app supports simple asset media URL references through `AssetMedia`, but not file upload, storage, processing, validation, or moderation.

### Pedigree OCR Gap

Current pedigree extraction is intentionally local/null and mockable only. It does not run real OCR, does not use background processing, and does not update linked birds until a dedicated pedigree description field or explicit product rule exists.

### Repo Cautions

- the service / validator / factory pattern is strongest in `apps.auctions`; `apps.users/services.py` now contains manager-permission helpers but `apps.users` still remains lighter than the auction domain
- naming/style drift exists and should not be fixed opportunistically:
  - `apps/assets/serializer.py` is singular
  - `ManagerProfileadminSerializer` casing is inconsistent
  - preserve existing public names unless a task explicitly asks for renames
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

### Phase A - PaymentRequest Completion Hardening

Goal:

Harden the manual sale completion MVP already implemented through `apps.payments`.

Includes:

- fix current correctness and security issues
- improve observability around PaymentRequest completion
- extend the current complaint/dispute MVP only when explicitly prioritized
- add administrative review tools around disputes, not payment approval
- keep payment gateway, wallet, proof upload, handoff, and shipping out of scope unless product policy changes

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
- refinements to the shared PaymentRequest completion flow
- stock adjustment rules beyond the current market payment approval behavior if product policy changes
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
- QR transfer entrypoint beyond the current public identity URL
- pedigree reader later in a simple manual-assisted phase first

### Phase F - Engagement Layer

Goal:

Add growth and retention features.

Includes:

- reward point automation
- expanded cashback rules beyond the current internal-balance MVP
- expanded badge automation
- expanded offer and promotion campaigns beyond the current marketplace discount/cashback MVP
- notifications beyond current placeholders
- realtime or expanded chat beyond the current REST-only direct chat MVP

## Immediate Next Sprint Direction

Current next sprint priority:

### PaymentRequest Completion + Core Fixes

Required tasks:

1. fix critical auction/auth bugs already present
2. enforce auction locking/edit restrictions
3. harden the existing PaymentRequest flow
4. extend dispute/complaint handling beyond the current MVP only when product scope requires it
5. improve audit/reporting around payment request lifecycle
6. keep seller order-item approval separate from sale completion

Do not prioritize reward point automation, QR, realtime or expanded chat, or expanded promotions before this phase is complete.

## Payment Approach

Important project decision:

- no gateway integration
- no Apple Pay, Google Pay, or card processing for buyer-seller marketplace sales
- no automated charge or capture
- no in-app financial processing
- no escrow
- no marketplace wallet
- no admin payment approval in the current MVP

Current expected flow:

1. auction winner is determined or market order item is approved by seller
2. buyer creates a `PaymentRequest`
3. seller approves or rejects the request
4. seller approval finalizes the sale inside the system
5. market stock is reduced for market requests
6. bird ownership transfers for bird assets
7. either related party can create a complaint after approval or rejection; managers review complaints manually without automatic financial or ownership reversal

This project should behave as a marketplace coordination system, not a payment processor.

## Shipping / Handoff Approach

Important project decision:

There is no shipping or handoff model in the current MVP.

Current MVP policy:

- no delivery tracking
- no buyer receipt confirmation
- no logistics provider integration
- seller `PaymentRequest` approval is the current MVP sale-completion signal
- do not let future payment prompts implement shipping or handoff work unless explicitly requested

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
  - notification placeholders and later notification delivery
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
- i18n tests: `pytest tests/test_i18n.py -q`
- pre-commit pytest hook runs `pytest -q`
- for future work, start with targeted tests in the touched app
- add broader regression tests only when changes cross module boundaries or alter shared behavior

## Default Assumptions For Future Work

- this file is both a current-state reference and a clearly labeled roadmap reference
- current implementation facts remain authoritative for present behavior
- roadmap items guide future implementation but are not implemented until the code exists
- existing behavior wins unless the user explicitly asks to change it
- when adding new backend workflows, prefer the auctions layering style as the default reference pattern

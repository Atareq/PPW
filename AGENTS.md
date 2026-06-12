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
- customer follow relationships for sellers and deterministic home feed MVPs
- future media, pedigree, expanded QR, reviews, reward point automation, realtime chat, and expanded promotion modules

PPW is a marketplace coordination system, not a fintech system and not a shipping-integrated platform.

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
- `apps.payments`
- `apps.promotions`
- `apps.complaints`
- `apps.chat`
- `apps.social`
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
- `build_token_for_user()` uses the stricter `user.active_profile` property
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

- `active_profile` requires `is_active=True`, `is_valid=True`, `activated=True`, and a non-deleted profile
- `active_profile` raises `PermissionDenied` when no current valid active profile exists
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
- fuller media/file handling around the existing simple image reference model

Important note:

This app currently models domain assets and simple image references, not a full media pipeline.

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
- `Asset.thumbnail_url` has been removed; asset media are represented by related `AssetMedia` rows
- current asset statuses are:
  - `available`
  - `sold`
  - `inactive`
- `Bird`, `Supply`, `Accessory`, `Feed`, and `Supplement` are subclasses of `Asset`
- `Bird.public_id` is a stable UUID for public bird identity and QR payload URLs
- `apps.general.models.Media` is a concrete base media-reference model with `media_url`, `media_type`, and optional `alt_text`
- `Media.media_type` currently supports `image` and `video`; existing and omitted media types default to `image`
- `apps.general.models.AssetMedia` is a concrete `Media` subclass linked to `Asset` through `asset.media`
- `AssetMedia` adds `is_primary` and `order`; ordering is by `order`, then `created`

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
- `images` and `image_url` are no longer accepted product media API field names
- `thumbnail_url` is no longer accepted in product asset API payloads
- create sets `owner` from `request.user.active_profile`
- seller product creation goes through `ProductAssetCreationService.create_product()`
- seller-created `Supply`, `Accessory`, `Feed`, and `Supplement` assets consume `Package.product_cost` from the seller's active `AssetSellerPackage`, even when `is_market_listed=False`
- manager-created product assets bypass seller package consumption and create no `PackageUsageLog`
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
- order item detail asset summaries use nested `media` from `AssetMedia`; they no longer expose `thumbnail_url`
- checkout and direct `POST /api/orders/` validate stock but do not decrement `Asset.count`
- checkout and direct `POST /api/orders/` do not mark assets `sold` and do not unlist assets from the market
- checkout and direct `POST /api/orders/` calculate `gross_total`, apply the best active `DiscountOffer`, store `discount_offer` and `discount_amount`, and store discounted `total_price`
- checkout and direct `POST /api/orders/` do not redeem or earn cashback
- new order items start as `pending_seller`
- seller approval changes `pending_seller` to `approved` without decrementing stock, marking assets sold, or unlisting assets
- seller approval checks active `CashbackOffer` eligibility, stores `cashback_offer`, `cashback_redeemed_amount`, and `cashback_earned_amount`, and updates `OrderItem.total_price`
- seller approval redeems cashback from `CustomerProfile.cashback_balance` only through `CashbackService` using `transaction.atomic()`, `select_for_update()`, and a `CashbackTransaction`
- seller approval does not revalidate stock at approval time
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

This app is not a payment gateway, wallet, refund system, proof-upload flow, handoff model, shipping integration, or complaint/dispute system. It records manual buyer/seller confirmation state only. Complaint/dispute records are owned by `apps.complaints`.

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
- auction payment request amount comes from `AuctionItem.current_price`
- marketplace payment requests require an approved `OrderItem` owned by the active customer profile
- marketplace payment request amount comes from `OrderItem.total_price`
- only one pending or rejected request per buyer and asset is allowed at a time
- buyers can update pending or rejected requests only; updating a rejected request resubmits it as pending
- approved and cancelled payment requests cannot be updated through the service
- sellers can approve or reject only their own pending payment requests
- seller approval marks the request approved and immutable
- seller approval completes the source marketplace `OrderItem` and refreshes the parent order status
- seller approval for market requests locks the source asset, requires enough current stock, decrements `Asset.count`, and marks zero-count assets `sold` and unlisted
- seller approval for market requests credits earned marketplace cashback once through `CashbackService.earn_for_completed_order_item()` after the source `OrderItem` is completed
- seller approval transfers ownership only for bird assets
- seller approval transfers an auction item's `paired_bird` when present
- seller approval does not transfer ownership for supply, accessory, feed, or supplement assets
- seller rejection marks the request rejected and does not transfer ownership, complete order items, or mutate stock
- `PaymentRequestEvent` records are written only for created, updated, approved, and rejected events

### `apps.complaints`

Current responsibilities:

- simple complaint/dispute records tied to `PaymentRequest`
- buyer/seller complaint creation after a payment request is approved or rejected
- manager review transitions for open complaints
- creator cancellation of open complaints
- lightweight `ComplaintEvent` trace records

Important note:

This app is not a refund system, ownership rollback system, cashback reversal system, complaint chat, attachment upload flow, legal arbitration workflow, notification system, seller penalty system, or payment approval tool. It records manual dispute state only.

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
- only the complaint creator can cancel, and only while the complaint is `open`
- complaint actions do not mutate `PaymentRequest`, asset ownership, product stock, orders, cashback, or ratings
- `ComplaintEvent` records are written for created, in-review, resolved, rejected, and cancelled transitions

Current complaint service implementation names:

- `ComplaintService.create_complaint()` creates an open complaint after locking and validating the payment request
- `ComplaintService.mark_in_review()` moves an open complaint into manager review
- `ComplaintService.resolve()` resolves an open or in-review complaint
- `ComplaintService.reject()` rejects an open or in-review complaint
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

This app is not a realtime system. It does not implement WebSockets, Firebase, realtime database behavior, typing indicators, online presence, group chat, attachments, asset/order-linked conversations, moderation UI, blocking, message deletion, or frontend behavior.

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
- conversation creation rejects receiver profiles that are not active, activated, valid, or non-deleted
- conversation list is scoped to the active customer or seller profile only
- conversation list uses `Conversation.last_message` and `last_message_at` for latest-message summary and ordering
- message list is scoped to the conversation customer or seller only and orders by `created`, then `id`
- message list supports `after_id` for polling-style incremental fetches
- sending requires an active conversation and a sender who is the conversation customer or seller
- message receiver is derived as the other participant; clients do not submit receiver ids
- messages are text-only and capped at `Message.MAX_BODY_LENGTH`
- empty messages are rejected
- messages containing phone-like numbers, email addresses, URLs, WhatsApp references, `wa.me`, Telegram, or `t.me` references are rejected
- sending a message updates `Conversation.last_message` and `last_message_at`
- sending a message triggers `send_chat_message_notification(message_id)` through Celery
- notification enqueue failures are logged and do not roll back message creation
- the current notification task is a safe logging placeholder because there is no notification app/model yet

Current chat service implementation names:

- `conversation_queryset()` returns optimized conversation rows with customer, seller, and last-message relations
- `message_queryset()` returns optimized message rows with sender and receiver relations
- `ChatService.get_or_create_conversation()` resolves the customer/seller pair and returns the existing or new conversation
- `ChatService.send_message()` validates customer/seller participation, state, and contact-sharing rules, creates the message, updates conversation summary fields, and enqueues notification
- `ChatService.mark_messages_read()` marks unread messages for the reader profile as read
- `send_chat_message_notification()` is the Celery notification placeholder task

### `apps.social`

Current responsibilities:

- customer-to-seller follow relationships
- idempotent seller follow and unfollow endpoints
- active customer following-list endpoint
- cursor-based customer auction feed
- cursor-based customer market feed
- deterministic discovery fallback after followed-seller content

Important note:

This app is not a recommendation engine, notification system, realtime feed, seen-item tracker, ads/sponsored-content system, seller-room feed, frontend module, or public unauthenticated discovery feed.

Current social API:

- `/api/social/sellers/<seller_id>/follow/`
  - `POST`: authenticated active customer creates or reactivates a follow for an active seller
- `/api/social/sellers/<seller_id>/unfollow/`
  - `POST`: authenticated active customer deactivates a follow if present
- `/api/social/sellers/following/`
  - `GET`: authenticated active customer lists active followed sellers
- `/api/feed/auctions/`
  - `GET`: authenticated active customer gets followed-seller active auctions first, then discovery auctions
- `/api/feed/market/`
  - `GET`: authenticated active customer gets followed-seller market-listed products first, then discovery products

Current social behavior:

- `SellerFollow.follower` points to `CustomerProfile`
- `SellerFollow.seller` points to `SellerProfile`
- `SellerFollow.is_active` is toggled for unfollow/refollow; follows are not hard-deleted by the API
- only one `SellerFollow` row can exist per follower/seller pair
- customers cannot follow a seller profile on the same user account
- customers cannot follow inactive, invalid, unactivated, or deleted sellers
- repeated follow returns the existing active row or reactivates an inactive row
- repeated unfollow is safe and leaves the row inactive
- following lists exclude inactive, invalid, unactivated, or deleted sellers
- feed endpoints require an authenticated active customer profile
- auction feed content is limited to visible, currently active `AuctionStatus.ACTIVE` auctions from active valid sellers
- market feed content is limited to available, listed, positive-count supply/accessory/feed/supplement assets from active valid sellers
- feed ordering is stable by `created` descending, then `id` descending
- feed responses put `source="following"` results before `source="discovery"` results
- discovery excludes content from followed sellers and IDs already returned in the same response
- if followed content is exhausted or absent, discovery fills the remaining limit
- feed `limit` defaults to `20`, is capped at `50`, and must be at least `1`
- feed cursors are URL-safe base64 JSON with separate `following` and `discovery` positions plus `following_exhausted`
- invalid cursor payloads return a clean validation error
- feeds do not use `order_by("?")`, SQL random functions, seen-item tracking, personalization scores, or recommendation ranking

Current social service implementation names:

- `SellerFollowService.follow()` creates, returns, or reactivates a seller follow
- `SellerFollowService.unfollow()` idempotently marks a seller follow inactive
- `SellerFollowService.following_queryset()` returns active followed sellers for the active customer
- `FeedService.get_auction_feed()` builds the auction feed with following content first and deterministic discovery fallback
- `FeedService.get_market_feed()` builds the market feed with following content first and deterministic discovery fallback
- `FeedCursor` parses and encodes the two-source cursor state

### `apps.promotions`

Current responsibilities:

- admin-created immutable `DiscountOffer` records for marketplace order-item discounts
- admin-created immutable `CashbackOffer` records for automatic marketplace cashback earning and redemption
- customer `cashback_balance` storage on `CustomerProfile`
- immutable `CashbackTransaction` ledger records for every cashback balance movement
- admin cashback balance adjustments with audit trail
- customer/admin promotion and cashback APIs

Important note:

This app is not a payment gateway, withdrawal wallet, coupon-code system, cashback expiration engine, seller campaign tool, auction cashback module, or admin approval workflow. Cashback is an internal customer balance only.

Current promotions API:

- `/api/promotions/discount-offers/current/`
  - `GET`: customer-visible active discount offers
- `/api/promotions/cashback-offers/current/`
  - `GET`: customer-visible active cashback offers
- `/api/promotions/cashback/my/`
  - `GET`: customer lists their own cashback transactions
- `/api/promotions/cashback/balance/`
  - `GET`: customer retrieves their current cashback balance
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
- cashback balance mutations use `transaction.atomic()` and `select_for_update()` on `CustomerProfile`
- `CashbackService.refund_redeemed_for_order_item()` exists for future approved-item cancellation flows; current seller rejection and seller timeout paths do not call it because they only affect pending seller items

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
- `Auction.seller_package` is a nullable FK to `AssetSellerPackage`; existing rows and manager-created rows can be null
- auction creation requires future `start_time`, later `end_time`, and nested item payloads
- `AuctionCreateSerializer` owns auction creation field-shape checks such as future/later times, auto-extend minutes, deposit payment deadline, buy-now price, and required `paired_bird_id` for pair-style auctions
- `AuctionBusinessValidator` enforces item counts by auction type, seller bird ownership for seller-created auctions, pair gender rules, duplicate bird rejection, and overlapping active-auction bird rejection
- manager-created auctions bypass seller bird ownership validation because managers are using the administrative auction creation path
- `AuctionService.create_auction()` writes a `created` event
- seller auction creation pre-validates package availability, then creates the auction/items and consumes `Package.auction_cost` in the same DB transaction
- seller-created auctions are linked to the consumed `AssetSellerPackage`
- manager-created auctions bypass seller package consumption and keep `seller_package=NULL`
- auction update, cancellation, close, bidding, and buy-now do not consume package points
- `BidBusinessValidator` requires a customer profile, an active time window, an active item, starting-price compliance, and minimum increment compliance
- bid placement revalidates under an `AuctionItem` row lock before creating the bid
- bid placement updates `AuctionItem.current_price`, optionally auto-extends, and writes a `bid_placed` event
- auto-extend uses the module constant `AUCTION_AUTO_EXTEND_MINUTES = 10` as the trigger window and extends by `auction.auto_extend_minutes`
- `AuctionService.update_auction()` allows updates only when status is `active`, the start time is still in the future, and no bids exist
- scheduled closing is handled by Celery Beat through `auctions.tasks.close_expired_auctions`
- `AuctionService.close_expired_auctions()` selects active auctions with `end_time <= now`
- `AuctionService.close_auction()` is idempotent for already closed auctions
- closing an auction marks each item `sold` with winner data when bids exist, otherwise `unsold`, sets the auction to `closed`, and writes an `auction_ended` event
- closing an auction does not create `PaymentRequest` records automatically
- sold seller-owned auction items can be finalized through buyer-created payment requests
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
  - `GET`: manager lists seller package requests/history
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
- when manager activation replaces an existing active seller package for the same seller, the old package is deactivated by setting `activated=False` and `invalidated_at`
- a seller can have many historical packages but only one active activated seller package, enforced by a partial unique constraint
- manager cancellation is allowed only for pending seller packages and sets `cancelled`, `activated=False`, and `invalidated_at`
- there is no standalone deactivate endpoint; deactivation currently happens only as part of activating a replacement package
- `SellerPackageService.get_active_package()` validates `expires_at`; expired active packages are marked `exhausted`, `activated=False`, and invalidated before returning a not-found response
- `PackageService.consume_points()` supports `auction_created` and `product_created` only; `pedigree_created` is rejected as unsupported for now
- consumption uses `transaction.atomic()` and `select_for_update()` on the active `AssetSellerPackage`
- only an active, activated, unexpired `AssetSellerPackage` can be consumed
- package costs come from `Package.auction_cost` and `Package.product_cost`
- successful consumption decrements `SellerPackage.remaining_points` and writes one `PackageUsageLog`
- a package becomes `exhausted`, `activated=False`, and gets `invalidated_at` when remaining points reach `0` or are below both auction and product creation costs
- expired active packages are marked exhausted/invalidated before the validation error is returned
- missing active packages, insufficient points, and unsupported usage types do not write usage logs and do not decrement points
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

Important note:

This app is a rewards foundation only. It does not implement automatic reward earning, reward schedules, point purchase, cashback conversion, withdrawal, payment gateway behavior, notifications, leaderboards, or referral rewards. Marketplace discount and cashback offers live in `apps.promotions`, not `apps.loyalty`.

Current loyalty API:

- `/api/loyalty/points/`
  - `GET`: authenticated user retrieves their user-level point balance
- `/api/loyalty/points/transactions/`
  - `GET`: authenticated users list their own account point transactions; managers list all transactions by default
- `/api/loyalty/badges/`
  - `GET`: without `profile_id`, returns the static badge choice catalog
  - `GET ?profile_id=<id>`: manager-only badge award list for a specific profile
  - `POST`: manager-only badge award creation for a specific profile and badge type
- `/api/loyalty/badges/my/`
  - `GET`: authenticated user retrieves badge awards for the current active profile

Current point behavior:

- `PointWallet` belongs to `UserAccount`, not `Profile`
- each user can have one wallet
- wallets are created lazily by `PointService.get_or_create_wallet()`
- wallet balance is a non-negative integer
- `PointTransaction` belongs to both `PointWallet` and `UserAccount`
- `PointTransaction.profile` is optional and stores the active profile context that caused the transaction when applicable
- point transaction types are `earn`, `spend`, and `expire`
- point transactions require positive points, non-negative `balance_after`, and a non-empty reason
- `PointService.earn_points()`, `spend_points()`, and `expire_points()` run in DB transactions and lock the wallet row before changing balance
- spending and expiring points cannot make wallet balance negative
- optional nonblank `idempotency_key` values are unique across point transactions
- reusing an idempotency key for the same logical transaction returns the existing transaction without changing balance again
- reusing an idempotency key for a different logical transaction raises a validation error
- non-manager transaction lists are always scoped to `request.user`
- manager transaction lists are unscoped by default and support `user_id`, `profile_id`, `transaction_type`, and `source_type` filters

Current badge behavior:

- there is no `Badge` model
- `BadgeAward` belongs to `Profile`, not `UserAccount`
- `BadgeAward.badge_type` is a fixed choice field
- current badge types are:
  - `first_order`
  - `points_starter`
  - `seller_starter`
- badge metadata, names, descriptions, eligibility, criteria type, criteria value, and icon URL live in `apps.loyalty.constants`
- `BadgeAwardCreateSerializer` owns submitted `badge_type` choice validation for the manager award API
- badge eligibility is enforced by `BadgeService.award_badge()`
- `first_order` is eligible for `Customer` profiles
- `seller_starter` is eligible for `Seller` profiles
- `points_starter` is eligible for any profile type
- a profile can receive the same badge type once
- different profiles under the same user can receive the same badge type independently
- badge awards are idempotent per profile and badge type
- `/api/loyalty/badges/my/` returns only the current active profile's badge awards
- automatic badge criteria evaluation is stubbed only and does not award badges yet

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
- only one active activated seller package is allowed per seller
- activation and cancellation happen through service methods, not direct serializer field updates
- package point consumption is connected to seller-created auctions and seller-created product assets
- package point consumption is not connected to pedigrees yet
- manager-created auctions and product assets bypass package point consumption

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
- `/api/packages/`
- `/api/seller-packages/`
- `/api/promotions/`

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

There is no gateway payment integration.

Current approach:

- buyer creates a manual `PaymentRequest` for a won auction item or approved market order item
- seller approves or rejects the request manually
- seller approval finalizes the sale inside the system
- no automated settlement, no capture, no refund engine
- no admin payment review, no proof upload, no handoff model

### Promotions / Cashback

Current approach:

- discounts are admin-created and apply automatically to marketplace order items at order creation
- cashback offers are admin-created and apply automatically to marketplace order items at seller approval and sale completion
- cashback redemption deducts from `CustomerProfile.cashback_balance` when a seller approves an order item
- cashback earning credits `CustomerProfile.cashback_balance` only after the related market `PaymentRequest` is approved
- every cashback balance change must have a `CashbackTransaction` with an idempotency key
- cashback is not withdrawable money and is not connected to payment capture, refunds, or shipping

### Handoff / Delivery

There is no shipping integration.

Current approach:

- no handoff or delivery model exists
- no logistics provider integration exists
- seller approval of a `PaymentRequest` is the MVP completion confirmation

### Ownership

Current short-term approach:

- only current owner is stored now
- full ownership history is intentionally deferred
- ownership transfer must happen only via payment service approval logic
- seller-approved payment requests transfer ownership only for bird assets
- non-bird product assets keep seller ownership even after market stock is reduced
- do not mutate ownership casually from serializers or views

### Assets / Media

Current assets app is domain modeling plus simple asset image URL references, not full file/media handling.

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

Current product asset visibility:

- managers can list, retrieve, create, update, and delete all product assets
- manager-created products are owned by the manager active profile because create always sets `owner=request.user.active_profile`
- sellers can create, update, and delete only their own product assets
- sellers can read their own product assets plus market-listed product assets
- customers can read only market-listed product assets
- product safe-read visibility is scoped before django-filter filters and ordering run

### Seller Packages

- package plans are created and updated by managers
- sellers can request available package plans
- package activation is manual and manager-driven
- a seller can have only one active activated package at a time
- manager activation of a new package deactivates the seller's previous active package
- pending package requests can be cancelled by managers
- active packages cannot be cancelled directly in the current API
- active `AssetSellerPackage` points are consumed by seller auction creation and seller product asset creation
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
- `RaceResultListCreateViewSet` uses `HasAppAccess` with `permission_resource = "RACE_RESULT"`
- race and race-result viewsets set `allow_scoped_read_access = True` because readable race data is intentionally available to any authenticated role
- managers can create, update, delete, and bulk-create race data through the global manager bypass
- sellers and customers can list/retrieve races, list per-race results, and search race results only
- race write validation and rank recalculation live in serializers/services, not in permission classes

Current package permission pattern:

- `PackageViewSet` uses `HasAppAccess` with `permission_resource = "PACKAGE"`
- sellers can list packages, but queryset scoping limits them to `is_available=True`
- managers can list all packages and create/update package plans through the global manager bypass
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
- managers can create, activate, deactivate, and list offers, list all cashback transactions, and perform cashback adjustments
- sellers have no direct promotions permissions in this sprint
- promotion services still enforce concrete customer/manager profile requirements where endpoint-level permission is not enough

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
- simple asset media URL reference models through `Media` and `AssetMedia`
- bird entity, CRUD endpoint, and validation
- product asset CRUD for supplies, accessories, feeds, and supplements
- product asset filtering, search, and ordering
- seller package point consumption for seller-created auctions and product assets
- persistent customer cart and cart item API foundation for market flows
- checkout and compatibility direct-order APIs for fixed-price order creation
- order validators and services for stock-validating fixed-price order creation
- order-item discount snapshots through admin-created discount offers
- marketplace cashback balance, offer, transaction, redemption, earning, refund-helper, and admin-adjustment services
- seller item approval/rejection foundation
- seller-decision timeout auto-cancellation for stale pending market order items
- manual PaymentRequest flow for won auction items and approved marketplace order items
- market stock reduction during seller-approved market PaymentRequests
- bird ownership transfer during seller-approved PaymentRequests
- split asset/seller comments and customer asset/seller ratings as a simple trust layer
- nested rating comment creation with `is_rating_comment=True`
- cached rating aggregates on assets and seller profiles
- lightweight order events for future notification hooks
- auctions, auction items, bids, and events
- auction creation, bidding, limited update locking, cancellation, buy-now, and close/winner-resolution flows
- service / validator / factory-style pattern in auctions, although the view still constructs the unsaved `Auction` before delegating persistence to `AuctionService.create_auction()`
- Dockerized local and production runtime configuration
- Redis + Celery worker/beat infrastructure for background jobs
- automatic scheduled auction closing through Celery Beat
- seller follow relationships for customer profiles
- cursor-based auction and market home feeds that rank followed-seller content before deterministic discovery fallback

Current API documentation coverage:

- mounted DRF routers, APIViews, and manual `GenericViewSet.as_view(...)` endpoints appear in the schema
- custom actions in auctions, auth, manual cart/checkout ViewSets, and order-item approval/rejection have targeted schema annotations
- schema tests cover auth policy for `/api/schema/`, Bearer security output, cart/checkout paths, and representative custom/manual endpoints

The codebase does not yet fully implement:

- media/file upload, storage, processing, or moderation subsystem beyond simple asset image URL references
- payment proof upload or admin payment review
- handoff or shipping workflow
- ownership history workflow
- dispute flow
- rating/comment moderation, replies, reports, likes, notifications, or analytics
- pedigree system
- pedigree reader
- expanded QR generation, printing, and transfer flows beyond the current public identity URL
- race results
- reward point automation and richer rewards programs
- promotion features beyond the current marketplace discount/cashback MVP, such as expiration, coupons, seller-created offers, auction cashback, usage caps, and complex campaign limits
- realtime or expanded chat beyond the current REST-only direct chat MVP
- notification delivery beyond lightweight order event and chat task placeholders
- pedigree package/quota enforcement as a finished business module

Important framing:

Current repo is a strong auction-oriented foundation, not a full product-complete backend yet.

Do not assume planned concepts already exist. Planned concepts must be labeled as planned, upcoming, or not yet implemented until code is present.

## Known Gaps / Known Risks

These risks are important and should be respected during future work.

### Auth / Permission Risks

- manager registration must not remain publicly open
- profile switching must respect activation and business constraints
- current `switch_user_profile()` can set an inactive/invalid/deleted target type active because it does not filter target profile validity before assignment
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
- package consumption is enforced only for `AssetSellerPackage`, not `PedigreeSellerPackage`
- product assets do not store a seller package FK; product traceability currently depends on `PackageUsageLog`
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
- related buyers and sellers can create simple complaints for approved or rejected payment requests; managers resolve or reject them manually

Important remaining gaps:

- no payment proof upload or admin payment review exists
- no handoff, shipping, or logistics workflow exists
- no ownership history table exists
- no automatic refund, ownership rollback, cashback reversal, complaint chat, complaint attachments, notifications, or seller penalty workflow exists

### Media Gap

Current assets app supports simple asset media URL references through `AssetMedia`, but not file upload, storage, processing, validation, or moderation.

### Repo Cautions

- the service / validator / factory pattern is strongest in `apps.auctions`; `apps.users/services.py` is currently empty
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
- no automated charge or capture
- no in-app financial processing
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
- pre-commit pytest hook runs `pytest -q`
- for future work, start with targeted tests in the touched app
- add broader regression tests only when changes cross module boundaries or alter shared behavior

## Default Assumptions For Future Work

- this file is both a current-state reference and a clearly labeled roadmap reference
- current implementation facts remain authoritative for present behavior
- roadmap items guide future implementation but are not implemented until the code exists
- existing behavior wins unless the user explicitly asks to change it
- when adding new backend workflows, prefer the auctions layering style as the default reference pattern

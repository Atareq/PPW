# PPW Backend AGENTS Guide

## Purpose

This file is the main backend engineering reference for PPW.

It has two jobs:

- document the current codebase truth so feature work stays aligned with the repo
- document clearly labeled planned direction for the coming phases without pretending those parts already exist

When current implementation and future intent differ, current code is the source of truth for behavior until the planned work is implemented.

## Project Summary

PPW is a Django + DRF backend for a pigeon marketplace and auction platform.

The platform supports and will continue to expand around:

- user authentication with multiple profile types
- bird ownership and bird listing
- auction creation and bidding
- admin-assisted post-auction completion
- future marketplace, pedigree, QR, reviews, points, chat, and promotion modules

PPW is a marketplace coordination system, not a fintech system and not a shipping-integrated platform.

Core business reality:

- no in-app payment gateway
- no automated shipping or logistics integration
- payment is manual and confirmed by admin
- handoff or delivery is manually confirmed by seller
- ownership transfer happens only after the required manual confirmations

The project must remain a modular monolith. Simplicity and correctness are preferred over feature richness and abstractions.

## Technology Stack

- Python
- Django
- Django REST Framework
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
- `db`: local PostgreSQL service in local Docker only
- `nginx`: production reverse proxy in production Docker only

Current container behavior:

- `docker/entrypoint.sh` waits for PostgreSQL before starting
- web role runs migrations and `collectstatic` before Gunicorn starts
- worker and beat roles reuse the same image and switch behavior through `APP_ROLE`
- local Docker uses `.env` and bind mounts the repo into `/app`
- production Docker uses `.env.prod` and serves static files through Nginx

### `apps.authentication`

Current responsibilities:

- custom user model
- login / registration
- JWT token creation
- active profile switching
- JWT profile-claim synchronization through `ProfileSyncJWTAuthentication`

Current auth behavior:

- `ppw/settings/base.py` uses `apps.authentication.authentication.ProfileSyncJWTAuthentication`
- JWTs must include a `profile` claim
- `build_token_for_user_role()` writes:
  - `profile`
  - `user_id`
  - `is_activated`
- a token without `profile` is rejected with authentication failure
- if token `profile` differs from `request.user.role`, authentication attempts `switch_user_profile(user, new_profile_type=token_profile)`
- if profile switching fails, authentication continues with the current active profile
- `switch_user_profile()` returns a `(success, profile_type)` style result

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

Current profile behavior:

- `active_profile` requires `is_active=True`, `is_valid=True`, and `activated=True`
- `Profile.is_valid` is the current punishment / disabled-profile flag
- exactly one active profile per user is enforced through the active-profile uniqueness constraint

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
- checkout-time stock reservation for fixed-price market order items
- lightweight order event records for future notification hooks

Important note:

This app currently provides the first cart and fixed-price checkout workflow layer. It does not yet implement payment review, handoff confirmation, settlement workflow, stock restoration after seller rejection, or ownership transfer workflow.

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
  - `POST`: customer converts the full active cart into an order and reserves stock
- `/api/orders/`
  - `GET`: customer lists own orders; manager lists all orders
  - `POST`: compatibility alias for direct order creation from market-listed seller assets; reserves stock immediately through the checkout service
- `/api/orders/<id>/`
  - `GET`: customer retrieves own order; manager retrieves any order
- `/api/orders/items/`
  - `GET`: seller lists own order items; manager lists all order items
- `/api/orders/items/<id>/approve/`
  - `POST`: seller approves their own pending order item
- `/api/orders/items/<id>/reject/`
  - `POST`: seller rejects their own pending order item

Current order behavior:

- each customer can have one active cart; active carts are created lazily
- customers can add only available, market-listed assets with enough stock from valid seller profiles
- adding the same asset to a cart increases the existing cart item quantity instead of creating a duplicate row
- duplicate `asset_id` entries in one order payload are rejected
- customers cannot order or cart assets owned by another profile on the same user account
- checkout runs in a DB transaction and locks the active cart plus all checked-out asset rows
- checkout revalidates cart items under lock and uses latest `Asset.price`, not stale cart price snapshots
- order creation stores `total_price` on `Order`, plus `unit_price_snapshot` and `total_price` on each item
- checkout and direct `POST /api/orders/` decrement stock immediately to reserve inventory
- when reserved stock reaches zero, the asset is marked `sold` and unlisted from the market
- new order items start as `pending_seller`
- seller approval changes `pending_seller` to `approved` only; it does not decrement stock again
- seller rejection changes `pending_seller` to `rejected` only; it does not restore stock in the current sprint
- aggregate order statuses are `pending`, `processing`, `completed`, `cancelled`, and `partial_rejected`
- if all order items are rejected or cancelled, the aggregate order status becomes `cancelled`
- any rejected item plus any non-rejected item makes the aggregate order status `partial_rejected`
- lightweight `OrderEvent` records are created for order creation, seller pending item notifications, and seller approval
- ownership transfer is not implemented yet

### `apps.auctions`

Current responsibilities:

- auctions
- auction items
- bids
- auction events
- auction business validation
- bidding orchestration
- settlement logic

This app currently has the clearest service / validator / factory layering in the repo and should be the reference pattern for future backend workflows.

### `apps.general`

Current responsibilities:

- base models
- permissions / authorization helpers
- shared constants / helpers

### Shared Abstractions

- `apps.general.models.BaseModel`: timestamped + soft-delete base model
- `apps.general.models.OwnerBaseModel`: common owner relation to `users.Profile`
- `apps.general.authorizations.HasAppAccess`: repo-specific permission gate used by app viewsets
- `HasAppAccess.allow_scoped_read_access`: opt-in read behavior for resources whose `get_queryset()` already scopes readable objects
- `HasAppAccess.permission_owner_field`: optional view attribute for object checks that use a non-`owner_id` ownership field, such as `OrderItem.seller_id`

### Main Domain Concepts

#### UserAccount

Authentication identity. A user may have multiple profiles. The active profile determines current behavior and permission context.

#### Profile

Profile is the real business actor in the system.

Current profile types:

- seller
- customer
- manager

Important rule:

Use `active_profile` as the operational actor in business flows.

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
- current event types include order creation, seller pending item, and seller approval
- there is no push delivery, async notification task, or external notification integration yet

#### Auction

Auction is the top-level aggregate for timed bidding. It contains one or more auction items. Auction type determines creation and validation rules.

#### AuctionItem

AuctionItem is the unit of bidding and sale.

Each item has:

- primary bird
- optional paired bird
- independent bid stream
- status
- winner data

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
- no editing of published or live auctions should be allowed once enforcement is complete
- birds should not be placed in overlapping active auctions
- auction state transitions must be explicit and safe

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
- customers can add listed seller assets to a persistent server-side cart
- customers can checkout the full active cart into a reserved-stock market order
- direct `POST /api/orders/` remains available as a compatibility alias with the same stock reservation semantics
- sellers can approve or reject their own order items
- checkout-time stock reservation decrements stock and can mark sold-out assets unavailable
- seller approval and rejection update item/order statuses but do not change stock in the current sprint
- lightweight order events exist as notification placeholders
- this does not mean market workflows are complete yet
- payment review, handoff, stock restoration after rejection, settlement, and ownership transfer are still not implemented

Current product asset visibility:

- managers can list, retrieve, create, update, and delete all product assets
- sellers can create, update, and delete only their own product assets
- sellers can read their own product assets plus market-listed product assets
- customers can read only market-listed product assets

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
- create reserved market order
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
- cart checkout and direct reserved market order creation
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
- avoid large refactors during feature work
- do not change unrelated files

### Layering

Preferred layering:

- models: structure and lightweight invariants
- serializers: request/data validation and representation
- validators: business validation
- services: orchestration and state changes
- views: HTTP layer only

### Permissions

Permissions must be checked at:

- endpoint level
- object level where relevant

Do not assume authenticated user is enough. Profile type and ownership matter.

Current permission resources:

- `BIRD`
- `PRODUCT_ASSET`
- `AUCTION`
- `ORDER`
- `ORDER_ITEM`

Current product asset permission pattern:

- use `HasAppAccess`
- set `permission_resource = "PRODUCT_ASSET"`
- set `allow_scoped_read_access = True`
- scope readable objects in `get_queryset()` before filters run

Current order permission pattern:

- `OrderViewSet` uses `HasAppAccess` with `permission_resource = "ORDER"`
- customers can list, retrieve, and create only their own orders through `Order.owner`
- cart and checkout APIViews require authentication plus an active customer profile
- sellers must switch to customer mode before using cart or checkout endpoints
- managers do not get cart or checkout access in the current sprint
- sellers do not access `/api/orders/`; seller order work happens through order-item endpoints
- `OrderItemViewSet` uses `HasAppAccess` with `permission_resource = "ORDER_ITEM"`
- order-item object checks use `permission_owner_field = "seller_id"`
- sellers can list, approve, and reject only their own sold order items
- managers can access all order and order-item actions through existing manager behavior

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
- bird entity and validation
- product asset CRUD for supplies, accessories, feeds, and supplements
- product asset filtering, search, and ordering
- persistent customer cart and cart item API foundation for market flows
- checkout and compatibility direct-order APIs for fixed-price order creation
- order validators and services for reserved-stock fixed-price order creation
- seller item approval/rejection foundation
- checkout-time stock reservation for market order items
- lightweight order events for future notification hooks
- auctions, auction items, bids, and events
- service / validator pattern in auctions
- Dockerized local and production runtime configuration
- Redis + Celery worker/beat infrastructure for background jobs
- automatic scheduled auction closing through Celery Beat

Current API documentation coverage:

- mounted DRF routers, APIViews, and manual `GenericViewSet.as_view(...)` endpoints appear in the schema
- custom actions in auctions, auth, cart, checkout, and order-item approval/rejection have targeted schema annotations
- schema tests cover auth policy for `/api/schema/`, Bearer security output, cart/checkout paths, and representative custom/manual endpoints

The codebase does not yet fully implement:

- media/file subsystem
- payment-backed market completion workflow
- payment review workflow
- handoff flow
- ownership transfer workflow as a formal process
- dispute flow
- ratings / reviews / comments
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
- token `profile` claim and active profile behavior must stay aligned
- test JWTs must include `profile` unless testing missing-claim behavior
- permission constants and object-level rules need tightening
- current authorization behavior for managers, superusers, and JSON permissions is not yet fully intuitive
- `allow_scoped_read_access` can expose data if used without correct `get_queryset()` scoping

### Auction Risks

- bid validation must be correct and re-checked under DB lock
- event types must be consistent
- auction editing restrictions must be enforced
- birds must not be reusable in conflicting active auctions
- settlement must enforce end-time and state rules

### Business Flow Gap

Biggest current missing layer:

- post-auction completion workflow

Without this, auctions can end technically but not complete business-wise.

Important related gap:

- marketplace cart, checkout, order creation, and seller approval now exist, but payment review, stock restoration after rejection, handoff, settlement, and ownership transfer do not

### Media Gap

Current assets app is domain modeling, not file/media handling.

### Repo Cautions

- the service / validator / factory pattern is strongest in `apps.auctions`; `apps.users/services.py` is currently empty
- naming/style drift exists and should not be fixed opportunistically:
  - `apps/assets/serializer.py` is singular
  - `ManagerProfileadminSerializer` casing is inconsistent
  - several files contain TODOs or mixed comment quality
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
- stock restoration or adjustment rules for rejected/resolved order items
- stronger stock and quantity handling around completion

### Phase D - Trust Layer

Goal:

Add social and business trust features.

Includes:

- ratings
- reviews
- comments
- moderation basics

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

Expected flow:

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

Expected flow:

- seller confirms that the item or bird has been handed over
- optional buyer confirmation may be introduced later
- system then completes ownership change

The platform records business confirmation states; it does not manage real shipping execution.

## Background Jobs & Time-Based Logic

### Celery Usage

Celery is now part of the current repo implementation.

Current implementation:

- `ppw/celery.py` initializes the Celery app
- `ppw/settings/base.py` configures broker, result backend, eager-mode test flags, and beat schedule
- Docker Compose runs `redis`, `celery_worker`, and `celery_beat`
- the shared container image switches behavior through `APP_ROLE`

Rules:

- do not execute time-based or heavy operations inside the request/response cycle
- use Celery for:
  - auction lifecycle updates and auction settling
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
  - status is active/live
  - `end_time <= now`
- transition them to `CLOSED` via service layer

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

## Guidance For Future Codex Work

When working on this repo:

- understand first, then change
- always plan briefly before coding and ask for approval
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

- current targeted auth baseline: `pytest tests/authentication -q` passes with `43 passed`
- current targeted asset baseline: `pytest tests/assets -q` passes with `16 passed`
- current targeted API docs baseline: `pytest tests/test_api_docs.py -q` passes with `5 passed`
- current targeted order baseline: `pytest tests/orders -q` passes with `35 passed`
- current schema validation baseline: `python manage.py spectacular --validate --settings ppw.settings.local --file /tmp/ppw-schema.yaml` passes with `0` errors and benign enum-name warnings
- current full-suite baseline: `pytest -q` passes with `165 passed`
- pre-commit pytest hook runs `pytest -q`; use the current full-suite baseline above as the direct pytest result
- for future work, start with targeted tests in the touched app
- add broader regression tests only when changes cross module boundaries or alter shared behavior

## Default Assumptions For Future Work

- this file is both a current-state reference and a clearly labeled roadmap reference
- current implementation facts remain authoritative for present behavior
- roadmap items guide future implementation but are not implemented until the code exists
- existing behavior wins unless the user explicitly asks to change it
- when adding new backend workflows, prefer the auctions layering style as the default reference pattern

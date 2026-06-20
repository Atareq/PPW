# PPW Backend — Change Log

> Format: each entry = one logical commit or session.
> **What** — the file(s) changed and what was added/modified.
> **Why** — the business or technical reason it was needed.

## 2026-06-20 — GAP-BE-2 & GAP-BE-3: Auction chat system + block check on bids

### GAP-BE-3: `apps/auctions/services.py`
**What:** Added `from apps.feed.services import BlockService` import. In `BidBusinessValidator._validate_bidder()`, added `BlockService.is_blocked_between(self.bidder, self.auction.owner)` check — raises `ValidationError("You cannot bid on this auction while a block is active.")` if the customer is blocked by (or has blocked) the auction seller.
**Why:** Requirement 9.4 — blocked buyer must not be able to bid. `BidBusinessValidator` had no block check; a blocked customer could place bids unimpeded.

### GAP-BE-2: `apps/auctions/models.py`
**What:** Added `chat_enabled = BooleanField(default=False)` field to `Auction`. Added new `AuctionComment` model with fields `auction` (FK), `sender` (FK → Profile), `body` (CharField max 500), `is_announcement` (BooleanField), and indexed on `(auction, created)` and `(sender, auction)`.
**Why:** Requirement 9.2 — seller-activatable side chat panel inside the auction page. No auction chat model existed.

### GAP-BE-2: `apps/auctions/migrations/0003_auction_chat.py`
**What:** Auto-generated migration adding `chat_enabled` to `Auction` and creating `AuctionComment`.
**Why:** Schema changes from the model above.

### GAP-BE-2: `apps/auctions/services.py`
**What:** Added module-level `_AUCTION_CHAT_CONTACT_PATTERNS` (same 4 regex patterns as `apps/chat/services.py`). Added `AuctionChatService` with:
- `list_comments(auction_id)` — returns queryset ordered by `(created, id)`
- `post_comment(auction, sender_profile, body, is_announcement)` — validates `chat_enabled`, validates sender eligibility (seller owner or customer with ≥1 bid on the auction), rejects `is_announcement=True` from non-sellers, rejects contact-info body
**Why:** Service layer encapsulates all auction chat business rules.

### GAP-BE-2: `apps/auctions/serializers.py`
**What:** Added `AuctionCommentSerializer` (response: `id`, `body`, `is_announcement`, `sender_id`, `sender_nickname`, `sender_type`, `is_mine`, `created`) and `AuctionCommentCreateSerializer` (input: `auction_id`, `body`, `is_announcement`). Added `chat_enabled` to `AuctionDetailSerializer.fields`. Added `chat_enabled` to `AuctionUpdateSerializer.fields`; its `update()` now pops and saves `chat_enabled` independently (sellers can toggle at any time, unlike title/description which are locked after auction starts).
**Why:** API contracts for the new endpoints and client visibility of the `chat_enabled` flag.

### GAP-BE-2: `apps/auctions/views.py`
**What:** Added `AuctionCommentViewSet(GenericViewSet)` — `list` (`GET /auctions/comments/?auction_id=X`) and `create` (`POST /auctions/comments/`) actions. Uses `permission_resource = "AUCTION_COMMENT"` and a `queryset` property for `HasAppAccess` compatibility.
**Why:** Exposes the auction chat endpoints.

### GAP-BE-2: `apps/auctions/urls.py`
**What:** Registered `AuctionCommentViewSet` at `r"comments"` in the router.
**Why:** Makes `GET /api/auctions/comments/` and `POST /api/auctions/comments/` reachable.

### GAP-BE-2: `apps/general/constants.py`
**What:** Added `"AUCTION_COMMENT": ["list", "create"]` to both `SELLER_PERMISSIONS` and `CUSTOMER_PERMISSIONS`.
**Why:** Both seller and customer profiles need access; without entries they'd get 403.

---

## 2026-06-20 — GAP-BE-1: Enforce follow check before chat conversation creation

### `apps/feed/services.py`
**What:** Added `SellerFollowService.is_following(customer_profile, seller_profile)` static method — a single `SellerFollow.objects.filter(..., is_active=True).exists()` query using `getattr` to accept both Profile instances and PKs.
**Why:** `ChatService` needed a reusable, tested point to check follow status without coupling chat to the ORM query internals of feed.

### `apps/chat/services.py`
**What:** Added `SellerFollowService` to the import from `apps.feed.services`. In `ChatService.get_or_create_conversation()`, added a `SellerFollowService.is_following(customer, seller)` check immediately after the existing block check — raises `ValidationError({"receiver_profile_id": "You must follow this seller before starting a conversation."})` if the customer does not actively follow the seller.
**Why:** Requirement 9.1d — "شرط المحادثة: أن يكون المشتري متابعاً للبائع". Previously the UI enforced this but a direct API call could bypass it. The backend now rejects unauthenticated conversation creation regardless of client behaviour.

---

## 2026-06-20 — Add seller discovery listing endpoint

### `apps/feed/serializers.py`
**What:** Added `SellerListSerializer` at the bottom of the file — fields: `id`, `nickname`, `country`, `avg_rating`, `ratings_count`, `avatar_url` (source=`photo`).
**Why:** New public seller listing endpoint needs a flat serializer; the existing `UserProfileSerializer` is too heavy for a grid card.

### `apps/feed/views.py`
**What:** Added `SellerListViewSet` (list-only action). Filters `Profile.objects.filter(profile_type='Seller', is_active=True)`. For customer callers, additionally excludes sellers the customer has blocked or been blocked by. Paginates with `page`/`page_size` query params (max 50 per page); returns `{results, count, page, page_size, has_more}`.
**Why:** No public sellers listing existed. `GET /api/users/mobile/sellers/` filters to the caller's own profile only (`get_queryset` uses `user=self.request.user`). The requirements and the غرف discovery tab both need a browseable directory of all active sellers.

### `apps/feed/urls.py`
**What:** Added `path("sellers/", SellerListViewSet.as_view(LIST_ACTION), name="seller-list")` inserted before the `people-you-may-know/` route.
**Why:** Exposes the new viewset at `GET /api/feed/sellers/`.

---

## 2026-06-18 — Fix ImproperlyConfigured error for AuctionListSerializer

### `apps/auctions/serializers.py`
**What:** Defined `current_price` as a `SerializerMethodField` and implemented `get_current_price` to fetch the first item's price.
**Why:** The `current_price` field was added to the `Meta.fields` list but was not defined as a field in `AuctionListSerializer` (since it is a field on `AuctionItem` and not on the `Auction` model directly). This was causing a 500 / `ImproperlyConfigured` error on all endpoints using `AuctionListSerializer` (e.g. `GET /api/auctions/`, `GET /api/auctions/active/`).

---

## 2026-06-18 — Notifications backend implementation

### `apps/core/models.py`, `apps/core/services.py`, `apps/core/serializers.py`, `apps/core/views.py`, `apps/core/urls.py`, `apps/core/migrations/0001_initial.py`
**What:** Added the `apps.core` notification module with `Notification`, `NotificationKind`, read/create serializers, `NotificationService`, list/create/mark-read/unread-count/destroy viewset actions, URL routes under `/api/core/notifications/`, and an initial migration.
**Why:** Mobile notification center was wired but the backend `/api/core/notifications/` endpoints did not exist.

### `ppw/settings/base.py`, `ppw/urls.py`
**What:** Registered `apps.core` in `APPS` and included `apps.core.urls` at `api/core/`.
**Why:** Makes the notification endpoints available to the mobile API.

### `apps/general/constants.py`
**What:** Added `NOTIFICATION: ["list", "mark_read", "unread_count"]` to both seller and customer permission matrices.
**Why:** Seller/customer mobile users need scoped access to their own notifications and unread count.

### Programmatic notification triggers
**What:** Created notifications for order checkout seller alerts, seller approve/reject/timeout customer alerts, payment approve/reject customer alerts, new auction bids, auction won/buy-now, chat message delivery, and new badge awards.
**Why:** The notification center needs real records from existing customer/seller workflows, not only manager-created manual records.

### `../MOBILE_BACKEND_AUDIT.md`
**What:** Marked Notifications complete, removed the open backend gap, and updated the module section and implementation priority.
**Why:** Notifications are no longer a mock-ready/mobile-only feature; the backend module is implemented.

**Verification:** `python manage.py check` passes. `python manage.py makemigrations --check --dry-run` shows no pending `core` migration changes; it still reports an unrelated pre-existing complaints migration drift.

---

## 2026-06-18 — Mobile promotions parity audit (customer scope)

### Promotions mobile-facing endpoints
**What:** Rechecked `GET /api/promotions/discount-offers/current/`, `GET /api/promotions/cashback-offers/current/`, `GET /api/promotions/cashback/balance/`, and `GET /api/promotions/cashback/my/` against the Flutter market and cashback screens.
**Why:** Mobile promotion reads are customer-facing. Sellers are affected by discounted/cashback order totals, but offer management and cashback adjustments are manager/dashboard scope.

### `pigeon_planet/lib/features/promotions/model/cashback_transaction_model.dart`
**What:** Cashback transactions now read backend `note` as the display description, recognize `refunded` and `admin_adjustment`, and treat refunds/positive admin adjustments as credits.
**Why:** Backend `CashbackTransactionSerializer` sends `note` and `transaction_type` values `earned`, `redeemed`, `refunded`, and `admin_adjustment`; mobile previously looked for `description` and `adjustment`.

### `pigeon_planet/lib/features/promotions/view/pages/cashback_history_page.dart`
**What:** Transaction tile credit/debit display now uses `transaction.isCredit`.
**Why:** Keeps UI sign/color behavior aligned with the model and backend transaction types.

### `../MOBILE_BACKEND_AUDIT.md`, `BACKEND_CHANGELOG.md`
**What:** Updated module progress, critical fixed-bug list, Promotions section scope notes, and this changelog entry.
**Why:** Audit now distinguishes customer promotion endpoints from seller indirect order impact and manager-only dashboard actions.

---

## 2026-06-18 — Mobile ratings/comments parity audit (seller/customer scope)

### Ratings/comments mobile-facing endpoints
**What:** Rechecked `GET/POST /api/asset-ratings/`, `GET/POST /api/seller-ratings/`, `GET/POST /api/asset-comments/`, and `GET/POST /api/seller-comments/` against the Flutter ratings feature.
**Why:** Endpoint paths, filters (`asset_id`, `seller_id`), nested create payloads, and backend role rules already matched mobile seller/customer scope. Manager-only moderation is not counted as a mobile gap.

### `pigeon_planet/lib/features/home/view/pages/breeder_profile_page.dart`
**What:** Mounted `RatingsSection` on seller profile beneath the aggregate rating summary, using `RatingTargetType.seller` and customer-only `canRate`.
**Why:** Seller ratings/comments were implemented in the mobile feature layer but unreachable from the seller profile screen.

### `pigeon_planet/lib/features/auctions/view/pages/bird_detail_page.dart`
**What:** Replaced the static empty reviews placeholder with live asset `RatingsSection` for the bird asset.
**Why:** Bird detail previously rendered no backend ratings/comments even though `/asset-ratings/` and `/asset-comments/` were wired.

### `pigeon_planet/lib/features/market/view/pages/product_detail_page.dart`
**What:** Added live asset `RatingsSection` for market products when the product ID can be parsed as an asset ID.
**Why:** Backend asset ratings apply to products as well as birds; product detail only showed aggregate rating counts before.

### `../MOBILE_BACKEND_AUDIT.md`, `BACKEND_CHANGELOG.md`
**What:** Updated module progress, critical fixed-bug list, Ratings & Comments section notes, and this changelog entry.
**Why:** Audit now reflects the 2026-06-18 seller/customer ratings/comments parity pass.

---

## 2026-06-18 — Mobile races/packages parity audit (seller/customer scope)

### Races mobile-facing endpoints
**What:** Rechecked `GET /api/races/`, `GET /api/races/<id>/`, `GET /api/races/<id>/results/`, and `GET /api/race-results/?q=...&include_race=true` against the Flutter races feature.
**Why:** Read-only seller/customer race browsing and result search are aligned. Manager-only race/result mutation remains dashboard scope.

### `pigeon_planet/lib/features/races/viewmodel/races_event.dart`, `pigeon_planet/lib/features/races/viewmodel/races_state.dart`, `pigeon_planet/lib/features/races/viewmodel/races_bloc.dart`
**What:** Added `RacesFilterChanged`, stored active `seasonYearFilter` / `stationNameFilter` in state, and passed filters into `getRaces()` for initial filtered load and paginated load-more.
**Why:** Backend and datasource already supported `season_year` and `station_name`, but the BLoC did not expose a user-driven filter flow.

### `pigeon_planet/lib/features/races/view/pages/races_page.dart`
**What:** Added visible season/station filter fields with apply and clear controls above the race list.
**Why:** Completes the mobile seller/customer UI for backend race filters.

### `apps/packages/views.py`, `apps/packages/urls.py`
**What:** Added seller-owned pending package cancel endpoint at `POST /api/packages/my-packages/<id>/cancel/` via `MySellerPackageViewSet.cancel`. The view uses the existing seller-scoped queryset and `SellerPackageService.cancel_package()`.
**Why:** Flutter already calls `POST /packages/my-packages/<id>/cancel/` from `PackageCancelRequested`, but the backend had no seller/mobile route registered for it.

### `apps/general/constants.py`
**What:** Added `cancel` to seller `MY_SELLER_PACKAGE` permissions.
**Why:** The new mobile cancel action needs to pass the app permission layer for seller profiles.

### `tests/packages/test_packages.py`
**What:** Added coverage for a seller cancelling their own pending package and for a seller being unable to cancel another seller's package.
**Why:** Protects ownership scoping for the new mobile endpoint.

### `pigeon_planet/lib/features/subscription/model/subscription_package_model.dart`
**What:** `PendingSellerPackageModel` now reads backend `created` with `created_at` fallback.
**Why:** Backend `SellerPackageSerializer` sends `created`, not `created_at`.

### `pigeon_planet/lib/features/subscription/viewmodel/packages_bloc.dart`
**What:** After a successful package request, the bloc refreshes `fetchPendingPackage()` and stores it in state.
**Why:** The pending package banner now appears immediately after `POST /packages/my-packages/` succeeds.

### `../MOBILE_BACKEND_AUDIT.md`
**What:** Updated module progress, Races section, Packages section, and stale implementation-priority item for the 2026-06-18 races/packages parity pass.
**Why:** Audit now reflects actual mobile seller/customer endpoint coverage.

---

## 2026-06-18 — Mobile loyalty parity audit (seller/customer scope)

### Mobile-facing loyalty endpoints
**What:** Rechecked the backend loyalty module against the Flutter seller/customer interface only. No backend endpoint change was required for mobile scope: `GET /api/loyalty/points/`, `GET /api/loyalty/points/transactions/`, `GET /api/loyalty/badges/`, and `GET /api/loyalty/badges/my/` already cover the mobile reads.
**Why:** Confirms manager-only loyalty behavior is dashboard scope and should not be counted as a mobile seller/customer gap.

### `pigeon_planet/lib/features/home/viewmodel/home_bloc.dart`
**What:** Customer mode now loads `GET /loyalty/points/` through `fetchLoyaltyBalance()`; seller mode keeps the combined visible balance through `fetchBalance()`.
**Why:** Customer home/FAB balance previously stayed at `0` because points were only fetched in seller mode.

### `pigeon_planet/lib/features/home/model/datasources/points_remote_datasource.dart`
**What:** `BadgeCatalogItem` now extracts `criteria_value.threshold` from the backend JSON shape. `fetchSnapshot()` now accepts `includePackageBalance` so callers can skip seller package lookup.
**Why:** Badge thresholds were parsed as missing/zero, and customer loyalty snapshots should not call seller-only package endpoints.

### `pigeon_planet/lib/features/home/view/widgets/points_system_modal.dart`, `pigeon_planet/lib/features/home/view/pages/home_page.dart`
**What:** `PointsSystemModal.show()` now receives `isSeller`; the modal passes that into `fetchSnapshot(includePackageBalance: isSeller)`.
**Why:** Customer loyalty modal now uses loyalty wallet data only, while seller mode still shows loyalty + active package points.

### `../MOBILE_BACKEND_AUDIT.md`, `BACKEND_CHANGELOG.md`
**What:** Updated module progress, critical fixed-bug list, Loyalty section notes, and this changelog entry for the 2026-06-18 mobile loyalty parity pass.
**Why:** Audit status now matches the files and explicitly scopes mobile parity to seller/customer endpoints.

---

## 2026-06-18 — Gap-analysis implementation (BE + dashboard)

### `apps/auctions/serializers.py` — `AuctionUpdateSerializer`, `AuctionItemDetailSerializer`
**What:** Added `winner_payment_request_id` (SerializerMethodField, returns linked `AuctionPaymentRequest.pk` via `related_name="payment_request"`). Added `is_visible` to `AuctionUpdateSerializer.Meta.fields`; `validate()` guards write to managers only; `update()` pops and saves `is_visible` directly on the instance before delegating the rest to `AuctionService.update_auction()`.
**Why:** Dashboard auction detail needed a direct link from a closed item to its payment request. Manager dashboard needed to toggle auction visibility.

---

### `apps/promotions/views.py` — `CashbackBalanceViewSet.retrieve`
**What:** Extended to support `?customer=<profile_id>` when caller is a manager — resolves the `CustomerProfile`, computes balance, and returns it. Non-customer callers without the param get `PermissionDenied`.
**Why:** Dashboard customer detail page needs to read cashback wallet balance via manager auth — previously the endpoint rejected any non-customer caller.

---

### `apps/orders/views.py` — `OrderItemViewSet`
**What:** Added `filter_backends = [DjangoFilterBackend]` and `filterset_fields = ["discount_offer", "cashback_offer"]`.
**Why:** Dashboard and analytics need to filter order items by offer ID.

---

### `apps/loyalty/views.py` — `PointBalanceAPIView`
**What:** Added `?user_id=<id>` support for managers. Resolves `UserAccount` by PK, calls `PointService.get_balance(target_user)`, returns balance with `user_id` in response. Non-manager callers attempting to use `?user_id` get `PermissionDenied`.
**Why:** Dashboard loyalty tab for a given customer needs a point-balance summary — the prior endpoint only served the authenticated user's own balance.

---

### `apps/users/views.py` — `SellerProfileAdminViewSet`, `CustomerProfileAdminViewSet`
**What:** Added three bulk `@action` endpoints to each viewset: `POST bulk-suspend/`, `POST bulk-restore/`, `POST bulk-activate/`. Each accepts `{"ids": [...]}` and returns `{"updated": N}`.
**Why:** Manager dashboard needed to act on multiple profiles at once — previously only single-profile PATCH was available.

---

### `apps/complaints/models.py`, `apps/complaints/services.py`, `apps/complaints/views.py`, `apps/complaints/urls.py`
**What:** Added `ComplaintEventType.REOPENED`. Added `ComplaintService.reopen()` — validates status is `resolved` or `rejected`, resets to `open`, clears `admin_note`, logs a `REOPENED` event. Added `ComplaintReopenViewSet` (POST). Registered at `POST /complaints/<id>/reopen/`.
**Why:** No recovery path existed for incorrectly-closed complaints.

---

## 2026-06-18 — Market module alignment (rating, ordering)

### `apps/assets/serializer.py` — `BirdSerializer`, `ProductAssetSerializer`
**What:** Added `rating` (float) and `review_count` (int) as `SerializerMethodField` to both serializers. Getters use annotation fallback: `getattr(obj, "_avg_rating", None)` / `getattr(obj, "_review_count", None)` for pre-annotated list querysets; fall back to single `aggregate(Avg("stars"))` / `.count()` call for non-annotated detail querysets.
**Why:** Mobile product and bird cards need to display star ratings without N+1 queries on list endpoints.

### `apps/assets/constants.py` — `PRODUCT_ASSET_FIELDS`, `PRODUCT_ASSET_READ_ONLY_FIELDS`
**What:** Added `"rating"` and `"review_count"` to both constant tuples.
**Why:** Constants drive the `Meta.fields` and `read_only_fields` for all product asset serializer subclasses — adding here applies to supplies, accessories, feeds, and supplements.

### `apps/assets/views.py` — `BirdViewSet`, `ProductAssetViewSet`
**What:** `BirdViewSet.queryset` now chains `.prefetch_related("asset_ratings").annotate(_avg_rating=Avg("asset_ratings__stars"), _review_count=Count("asset_ratings__id"))`. `ProductAssetViewSet.get_queryset()` receives the same annotation.
**Why:** Pre-computes aggregates at the DB level for the list endpoint, so `get_rating()`/`get_review_count()` on serializers read from the annotation rather than issuing per-object queries.

---

## 2026-06-18 — Auctions module alignment

### `apps/auctions/serializers.py` — `BirdAuctionSerializer` (new)
**What:** New serializer with full bird fields: `id`, `name`, `ring_number`, `gender`, `colour`, `birthday`, `achievements`, `flying_speed`, `stamina_ability`, `description`, `price`, `media` (nested `AssetMediaSerializer`), `seller_id`, `seller_nickname`.
**Why:** `AuctionItemDetailSerializer` was using `BirdBasicSerializer` (6 fields, no media). The mobile item detail page could not display bird images, stats, or seller info.

### `apps/auctions/serializers.py` — `AuctionItemDetailSerializer`
**What:** Changed `bird` and `paired_bird` fields from `BirdBasicSerializer` to `BirdAuctionSerializer`.
**Why:** See above — full bird info including media is required by the mobile item detail page.

### `apps/auctions/serializers.py` — `AuctionDetailSerializer`
**What:** Added `is_owner` as a `SerializerMethodField` (`obj.owner_id == request.user.active_profile.pk`). Added `min_bid_increment` to `Meta.fields`.
**Why:** The mobile detail page rendered edit/cancel buttons based on `is_owner` — always `false` because the field was never sent. `min_bid_increment` was present on the model but not serialized; the mobile bid input always defaulted to 1.00.

### `apps/auctions/serializers.py` — `AuctionListSerializer`
**What:** Added `is_owner` `SerializerMethodField`, `buy_now_enabled`, `min_bid_increment`, `current_price`, and `thumbnail_url` (`SerializerMethodField`, traverses first item → bird → media) to fields.
**Why:** List/feed cards were missing price, thumbnail, and ownership flag. `is_owner` is needed on the "my auctions" seller list to show manage controls without a separate detail fetch.

### `apps/feed/services.py` — `FeedService.get_auction_feed`
**What:** Deepened prefetch from `"items"` to `"items__bird__media"`.
**Why:** `get_thumbnail_url` on `AuctionListSerializer` now traverses the full relation chain; without this prefetch every feed card triggered N+1 queries.

---

## 2026-06-18 — Feed & Chat & Permissions fixes

### `apps/auctions/serializers.py` — `AuctionListSerializer`
**What:** Added `current_price` to `Meta.fields`; added `thumbnail_url` as a `SerializerMethodField` that returns the first image URL from the first auction item's bird media.
**Why:** The mobile feed was showing 0 for price (field existed on the model but wasn't serialized) and a placeholder image for every card (no thumbnail was exposed). Feed cards are now populated with real data.

### `apps/feed/services.py` — `FeedService.get_auction_feed`
**What:** Changed `.prefetch_related("items")` to `.prefetch_related("items__bird__media")`.
**Why:** `AuctionListSerializer.get_thumbnail_url` traverses `auction → items → bird → media`. Without the deeper prefetch this produced N+1 queries on every feed page load.

### `apps/chat/serializers.py` — `ConversationListSerializer`
**What:** Added `unread_count` as a `SerializerMethodField`. Counts `Message` rows where `conversation=obj`, `receiver=active_profile`, `is_read=False` using the `active_profile` already in serializer context.
**Why:** The mobile conversation list badge was always showing 0 — the field did not exist in the serializer before this change.

### `apps/general/constants.py` — `SELLER_PERMISSIONS` / `CUSTOMER_PERMISSIONS`
**What:** Added `"list"` to the `PEDIGREE_DOCUMENT` action list in both permission dicts.
**Why:** `GET /pedigrees/documents/` was returning 403 for all mobile users. The `list` action was missing from the permission matrix even though the viewset already implemented `ListModelMixin`.

---

## 2026-06-17 — Mobile app initial alignment (bulk session)

### `apps/assets/serializer.py` — `BirdSerializer`, `ProductAssetSerializer`
**What:** Removed `images` and `thumbnail_url` write support (raises `ValidationError`). Write path now requires `media: [{media_url, media_type, is_primary, order}]` array.
**Why:** The old field names were removed from the schema but the mobile was still sending them, silently dropping all media on create/update.

### `apps/users/serializers.py` — `CustomerProfileSerializer`
**What:** Added `cashback_balance` to serializer output (was missing from read fields).
**Why:** Mobile profile page showed no cashback balance; the field exists on the model.

### `apps/general/constants.py` — various permission lists
**What:** Added missing actions (`retrieve`, `create`, `partial_update`) to several resources that had incomplete lists.
**Why:** Multiple mobile flows were hitting 403 because the permission matrix had incomplete action lists for resources like `COMPLAINT`, `PAYMENT_REQUEST`, `PEDIGREE_DOCUMENT`.

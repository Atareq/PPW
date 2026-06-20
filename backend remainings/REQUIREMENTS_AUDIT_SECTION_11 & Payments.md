# Requirements Audit — Section 11 (Admin Panel) & Payments

> Section: 11 — Admin Panel / Staff & Permissions / Marketing / Payments
> Audit date: 2026-06-20
> Status: Partial — Staff/Auth done; 11 items remaining

---

## ✅ Completed This Session

| # | Backend | Dashboard |
|---|---------|-----------|
| Staff role hierarchy (`super_admin`, `admin`, `moderator`, `finance_admin`, `support_agent`) | ✅ `StaffRole` + `STAFF_ROLE_PERMISSIONS` in `apps.users.constants` | ✅ `auth.store` + JWT claim |
| Role-scoped permission checking | ✅ `HasAppAccess` + `StaffPermissions.can_access()` | ✅ role-gated sidebar nav |
| Staff management API | ✅ `GET/POST /api/users/admin/staff/`, `PATCH/DELETE /api/users/admin/staff/<id>/` | ✅ `/staff` page — list, create, inline role edit, deactivate |
| Audit log model + service | ✅ `AuditLog` model, `AuditLogService.log()` | ✅ `/audit-logs` page — read-only table with filters |
| Audit log endpoint | ✅ `GET /api/audit-logs/` | ✅ connected |
| Manager registration locked to Super Admin | ✅ `ManagerRegisterAPIView` requires `HasStaffRole` | — |

---

## Remaining — Backend + Dashboard

---

### 1. Seller Verification Workflow

**App:** `apps.users`

#### Model — `SellerVerificationRequest`

| Field | Type | Notes |
|-------|------|-------|
| `seller` | FK → `SellerProfile` | `related_name="verification_requests"` |
| `status` | CharField | choices: `pending`, `approved`, `rejected`; default `pending` |
| `document_urls` | JSONField | list of uploaded file URLs |
| `reviewer` | FK → `ManagerProfile` | nullable |
| `reviewed_at` | DateTimeField | nullable |
| `admin_note` | TextField | blank |

#### Backend Endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `POST` | `/api/users/mobile/sellers/verification/` | Seller | Submit verification request |
| `GET` | `/api/users/admin/verification-requests/` | Moderator+ | List; filter by `status` |
| `GET` | `/api/users/admin/verification-requests/<id>/` | Moderator+ | Retrieve |
| `POST` | `/api/users/admin/verification-requests/<id>/approve/` | Admin+ | Approve → `SellerProfile.activated = True` |
| `POST` | `/api/users/admin/verification-requests/<id>/reject/` | Admin+ | Reject with `admin_note` |

#### Dashboard Page — `/verification-requests`

- Table: seller username, phone, status badge, submitted_at, document links
- Approve / Reject buttons with admin_note input on reject
- Filter by status (`pending` / `approved` / `rejected`)

---

### 2. Auction Admin Edit (Deadline & Type)

**App:** `apps.auctions`

#### Backend Changes

- Add `AdminAuctionUpdateSerializer` exposing `end_time`, `auction_type`, `title`, `description`, `tags` — no bid-count restriction for managers
- Update `AuctionAdminViewSet.partial_update()` to use it when actor is Manager
- Log via `AuditLogService.log()` on each change

#### Backend Endpoint

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `PATCH` | `/api/auctions/admin/<id>/` | Moderator+ | Edit `end_time`, `auction_type`, title, tags on any auction |

#### Dashboard

- Add editable fields (`end_time`, `auction_type`) to the existing `AuctionDetailPage` for managers

---

### 3. Prize Campaigns / Seasonal Awards

**App:** `apps.loyalty`

#### Models

**`PrizeCampaign`**

| Field | Type | Notes |
|-------|------|-------|
| `title` | CharField | |
| `description` | TextField | blank |
| `prize_type` | CharField | choices: `points`, `badge`, `cashback` |
| `prize_value` | DecimalField | |
| `badge_type` | CharField | nullable |
| `target_audience` | CharField | choices: `all`, `seller`, `customer` |
| `start_date` | DateField | |
| `end_date` | DateField | |
| `status` | CharField | choices: `active`, `ended`; default `active` |
| `created_by` | FK → `ManagerProfile` | |

**`PrizeAward`**

| Field | Type | Notes |
|-------|------|-------|
| `campaign` | FK → `PrizeCampaign` | |
| `profile` | FK → `Profile` | |
| `awarded_at` | DateTimeField | auto |
| `value_snapshot` | JSONField | |

#### Backend Endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET/POST` | `/api/loyalty/campaigns/` | Finance Admin+ | List / create campaigns |
| `GET/PATCH` | `/api/loyalty/campaigns/<id>/` | Finance Admin+ | Retrieve / update |
| `POST` | `/api/loyalty/campaigns/<id>/award/` | Admin+ | Manually award; body: `{ "profile_id": "..." }` |

#### Dashboard Page — `/loyalty` (extend existing)

- Add Campaigns tab: list with status + date range
- Create campaign modal (prize type, value, audience, dates)
- Award button → profile ID input

---

### 4. Commission / Earnings Ledger

**App:** `apps.payments`

#### Model — `CommissionLedger`

| Field | Type | Notes |
|-------|------|-------|
| `transaction_type` | CharField | choices: `auction`, `market` |
| `payment_request` | FK → `PaymentRequest` | nullable |
| `seller` | FK → `SellerProfile` | |
| `gross_amount` | DecimalField | |
| `commission_rate` | DecimalField | e.g. `0.05` = 5% |
| `commission_amount` | DecimalField | computed |

Written automatically inside `PaymentRequest` approval service.

#### Backend Endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET` | `/api/commissions/` | Finance Admin+ | List; filters: `seller`, `transaction_type`, `date_after`, `date_before` |
| `GET` | `/api/commissions/summary/` | Finance Admin+ | Aggregate totals by type + period |

#### Dashboard Page — `/commissions`

- Summary cards: total commissions, auction vs market split
- Paginated ledger table with filters (date range, seller, type)

---

### 5. Platform Settings

**App:** `apps.general`

#### Model — `PlatformSetting`

| Field | Type | Notes |
|-------|------|-------|
| `key` | CharField | unique slug |
| `label` | CharField | human-readable |
| `value` | JSONField | the setting value |
| `updated_by` | FK → `ManagerProfile` | nullable |

Seed rows: `supported_languages`, `supported_currencies`, `payment_methods`, `shipping_policies`, `data_retention_days`

Add `PLATFORM_SETTINGS` to `STAFF_ROLE_PERMISSIONS` → `super_admin`, `admin`: `["list", "retrieve", "partial_update"]`

#### Backend Endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET` | `/api/settings/` | Any Manager | List all settings |
| `GET` | `/api/settings/<key>/` | Any Manager | Retrieve one |
| `PATCH` | `/api/settings/<key>/` | Admin+ | Update value |

#### Dashboard Page — `/settings`

- Key-value editor; each row is editable inline for Admin+
- Shows `updated_by` + `updated_at` per row

---

### 6. Admin Reports & Analytics

**App:** `apps.insights` (extend) or new `apps.reports`

No new models — queries against existing data.

Add `REPORTS` to `STAFF_ROLE_PERMISSIONS` → `super_admin`, `admin`: `["list"]`

#### Backend Endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET` | `/api/reports/overview/` | Admin+ | Total users, auctions, sales, revenue; params: `date_after`, `date_before` |
| `GET` | `/api/reports/users/` | Admin+ | Active / suspended / new user counts by period |
| `GET` | `/api/reports/auctions/` | Admin+ | Active / ended / cancelled counts + bid volume |
| `GET` | `/api/reports/staff-activity/` | Super Admin | Audit log aggregated per staff member |

#### Dashboard — extend `OverviewPage`

- Add date range filter to existing stat cards
- Add staff activity table (Super Admin only section)

---

### 7. Extended Marketing Offer Types

**App:** `apps.promotions`

#### New Model — `MarketingOffer`

| Field | Type | Notes |
|-------|------|-------|
| `name` | CharField | |
| `offer_type` | CharField | choices: `commission_discount`, `free_highlight`, `double_points`, `temporary_badge` |
| `target_audience` | CharField | choices: `all`, `vip`, `seller`, `customer` |
| `value` | DecimalField | discount %, days, multiplier |
| `badge_type` | CharField | nullable |
| `start_date` | DateField | |
| `end_date` | DateField | |
| `conditions` | TextField | blank |
| `status` | CharField | choices: `active`, `inactive`; default `inactive` |
| `created_by` | FK → `ManagerProfile` | |

#### Existing Model Changes

- `BadgeAward`: add `expires_at` (DateTimeField, nullable)
- `Asset` + `Auction`: add `is_highlighted` (BooleanField) + `highlighted_until` (DateTimeField, nullable)

#### Service Hooks

| Offer Type | Hook Location |
|------------|---------------|
| `commission_discount` | `PaymentRequest` approval service |
| `free_highlight` | `Asset`/`Auction` create service |
| `double_points` | `PointService.earn_points()` |
| `temporary_badge` | `BadgeAwardService` |

#### Backend Endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET/POST` | `/api/promotions/admin/marketing-offers/` | Finance Admin+ | List / create |
| `GET/PATCH` | `/api/promotions/admin/marketing-offers/<id>/` | Finance Admin+ | Retrieve / update |
| `POST` | `/api/promotions/admin/marketing-offers/<id>/activate/` | Finance Admin+ | Activate |
| `POST` | `/api/promotions/admin/marketing-offers/<id>/deactivate/` | Finance Admin+ | Deactivate |

#### Dashboard — extend `/promotions`

- Add Marketing Offers tab next to existing Discount/Cashback tabs
- Offer type selector, audience selector, date range, conditions field

---

### 8. Announcement Bar

**App:** `apps.general`

#### Model — `AnnouncementBar`

| Field | Type | Notes |
|-------|------|-------|
| `text` | TextField | |
| `duration_hours` | PositiveIntegerField | |
| `is_active` | BooleanField | default `False` |
| `created_by` | FK → `ManagerProfile` | |
| `published_at` | DateTimeField | nullable |
| `expires_at` | DateTimeField | computed on publish |

One active announcement at a time; publish deactivates any previous.

#### Backend Endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET` | `/api/announcements/active/` | Public | Returns active announcement or `null` |
| `GET/POST` | `/api/announcements/admin/` | Admin+ | List / create |
| `GET/PATCH` | `/api/announcements/admin/<id>/` | Admin+ | Retrieve / update |
| `POST` | `/api/announcements/admin/<id>/publish/` | Admin+ | Publish + set expiry |
| `POST` | `/api/announcements/admin/<id>/unpublish/` | Admin+ | Deactivate immediately |

#### Dashboard Page — `/announcements`

- List with active badge + expires_at countdown
- Create form: text + duration_hours
- Publish / Unpublish buttons

---

### 9. Wheel / Spin Game

**App:** `apps.wheel` (new)

#### Models

**`WheelSegment`** — label, prize_type, prize_value, probability_weight, is_active, display_color, icon_url

**`WheelSpin`** — profile FK, segment FK (result), spun_at, prize_claimed

#### Backend Endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET/POST` | `/api/wheel/segments/` | Admin+ | List / create segments |
| `PATCH/DELETE` | `/api/wheel/segments/<id>/` | Admin+ | Update / remove |
| `POST` | `/api/wheel/spin/` | Customer / Seller | Spin (rate-limited: 1/24h per profile) |
| `GET` | `/api/wheel/my-spins/` | Customer / Seller | Spin history |

#### Dashboard Page — `/wheel`

- Segment list: label, prize type, weight, color, active toggle
- Add / edit segment modal
- Spin history table (read-only)

---

### 10. Sponsors

**App:** `apps.general`

#### Model — `Sponsor`

| Field | Type | Notes |
|-------|------|-------|
| `name` | CharField | |
| `logo_url` | URLField | |
| `link_url` | URLField | blank |
| `placement` | CharField | choices: `top_bar`, `auction_page`, `home_feed` |
| `start_date` | DateField | |
| `end_date` | DateField | |
| `is_active` | BooleanField | default `False` |
| `priority` | PositiveIntegerField | |

#### Backend Endpoints

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `GET/POST` | `/api/sponsors/admin/` | Admin+ | List / create |
| `GET/PATCH/DELETE` | `/api/sponsors/admin/<id>/` | Admin+ | Manage |
| `GET` | `/api/sponsors/active/` | Public | Active sponsors; filter: `?placement=` |

#### Dashboard Page — `/sponsors`

- Table with placement badge, active toggle, date range
- Create / edit modal

---

### 11. Payment Proof (Packages + Market + Auction)

**Apps:** `apps.packages`, `apps.payments`

#### A. Package Payment Proof

**Model Change — `SellerPackage`**

| Field | Type | Notes |
|-------|------|-------|
| `payment_proof_urls` | JSONField | list of file URLs; default `[]` |
| `payment_note` | TextField | blank |
| `amount_paid` | DecimalField | nullable |

**Endpoint**

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `PATCH` | `/api/packages/my-packages/<id>/submit-proof/` | Seller (owner) | Upload proof; only on `PENDING` packages |

#### B. Auction & Market Payment Proof

**Model Change — `PaymentRequest`**

| Field | Type | Notes |
|-------|------|-------|
| `payment_proof_urls` | JSONField | list of file URLs; default `[]` |

**Endpoint**

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| `POST` | `/api/payments/requests/<id>/submit-proof/` | Customer (buyer) | Attach proof URLs; resets `REJECTED` → `PENDING` |

#### Dashboard

- Seller Requests page: show `payment_proof_urls` as clickable links when reviewing a pending package
- Payments page: show `payment_proof_urls` in the payment request detail drawer

---

## Migration Plan

| # | Migration | App |
|---|-----------|-----|
| 1 | Add `SellerVerificationRequest` | `users` |
| 2 | Add `PrizeCampaign`, `PrizeAward` | `loyalty` |
| 3 | Add `CommissionLedger` | `payments` |
| 4 | Add `PlatformSetting` | `general` |
| 5 | Add `MarketingOffer`; `expires_at` on `BadgeAward`; `is_highlighted`/`highlighted_until` on `Asset`, `Auction` | `promotions`, `loyalty`, `assets`, `auctions` |
| 6 | Add `AnnouncementBar` | `general` |
| 7 | Create `apps.wheel`; add `WheelSegment`, `WheelSpin` | `wheel` |
| 8 | Add `Sponsor` | `general` |
| 9 | Add `payment_proof_urls`, `payment_note`, `amount_paid` to `SellerPackage` | `packages` |
| 10 | Add `payment_proof_urls` to `PaymentRequest` | `payments` |

---

## Recommended Build Order

| Priority | Item | Backend Effort | Dashboard Effort |
|----------|------|----------------|-----------------|
| 1 | Announcement Bar (#8) | Low | Low |
| 2 | Platform Settings (#5) | Low | Low |
| 3 | Sponsors (#10) | Low | Low |
| 4 | Payment Proof (#11) | Low | Low |
| 5 | Auction Admin Edit (#2) | Low-Medium | Low |
| 6 | Admin Reports (#6) | Medium | Medium |
| 7 | Commission Ledger (#4) | Medium | Medium |
| 8 | Extended Marketing Offers (#7) | Medium | Medium |
| 9 | Seller Verification (#1) | Medium | Medium |
| 10 | Prize Campaigns (#3) | Medium | Medium |
| 11 | Wheel / Spin Game (#9) | High | High |

# Uncommitted Changes — Staff Roles, Audit Log, Manager Permissions
**Status:** Unstaged / uncommitted as of 2026-06-20

---

## Summary of Changes

| Area | Change Type | Description |
|------|-------------|-------------|
| Users (Constants) | New | `StaffRole` enum + `STAFF_ROLE_PERMISSIONS` fine-grained access map |
| Users (Model) | Updated | `ManagerProfile` gets `staff_role` field; `SellerProfile` gets `description`; `unique_together` removed from `Profile` |
| Users (Serializers) | New | `StaffMemberListSerializer`, `StaffMemberCreateSerializer`, `StaffMemberUpdateSerializer` |
| Users (Serializers) | Updated | `SellerProfileMobileSerializer` now exposes `description` |
| Users (Views) | New | `StaffManagementViewSet` — full CRUD for staff accounts (Super Admin) |
| Users (Views) | Updated | Admin viewsets switched from `IsAdminUser` to `HasAppAccess`; `stats` action on seller admin; audit logging on all mutations; seller mobile create/get_object refactored |
| Authentication (Services) | Updated | `switch_user_profile` refactored; new `switch_user_profile_by_id`; JWT embeds `staff_role` for managers |
| Authentication (Views) | Updated | `ManagerRegisterAPIView` now requires Super Admin; `SwitchProfileAPIView` accepts `profile_id` |
| General (Authorizations) | New | `StaffPermissions` helper + `HasStaffRole` permission class |
| General (Authorizations) | Updated | `AuthorizationService` now checks manager's `staff_role` instead of granting all managers full access |
| General (Model) | New | `AuditLog` model |
| General (Services) | New | `AuditLogService.log()` — never-raising audit writer |
| General (Views/URLs) | New | `GET /api/audit-logs/` — filtered, paginated audit log list |
| Feed (Serializers) | Updated | `SellerListSerializer` adds `user_id` and `description` |

---

## 1. Users — `StaffRole` & Permission Map

**File:** `apps/users/constants.py`

### New `StaffRole` enum

| Value | Label |
|-------|-------|
| `super_admin` | Super Admin |
| `admin` | Admin |
| `moderator` | Moderator |
| `finance_admin` | Finance Admin |
| `support_agent` | Support Agent |

### `STAFF_ROLE_PERMISSIONS` map

Controls which `permission_resource` + action each role may access. Evaluated by `StaffPermissions.can_access()`.

| Role | Access |
|------|--------|
| `super_admin` | All resources, all actions |
| `admin` | All resources, all actions **except `destroy`** |
| `moderator` | Profiles (list/retrieve/partial_update/bulk-*), Auctions (list/retrieve/close/cancel), Complaints, Birds, Products, Pedigrees, Comments, Ratings |
| `finance_admin` | Packages, Promotions, Loyalty, Payment Requests (read/create/activate) |
| `support_agent` | Complaints, Profiles (read-only), Payment Requests (read-only), Comments (read-only) |

---

## 2. Users — Model Changes

**File:** `apps/users/models.py`

### `ManagerProfile` — new `staff_role` field
```
staff_role  CharField(30, choices=StaffRole)  default="admin"
```

### `SellerProfile` — new `description` field
```
description  TextField  blank=True
```

### `Profile` — `unique_together` removed
Previously `("user", "type")` was a `unique_together`. Replaced by a `UniqueConstraint` — allows a user to have multiple profiles of the same type (e.g. multiple seller rooms).

---

## 3. Users — Staff Management Serializers

**File:** `apps/users/serializers.py`

### `StaffMemberListSerializer` (read)
Fields: `id`, `username`, `phone_number`, `staff_role`, `is_active`, `is_valid`, `activated`, `created`, `modified`.

### `StaffMemberCreateSerializer` (write)

**Request:**
```json
{
  "username": "moderator01",
  "phone_number": "+201001234567",
  "password": "Str0ngPass!",
  "staff_role": "moderator"
}
```
Validates password strength, unique phone and username. Creates `UserAccount` (is_staff=True) + `ManagerProfile` atomically.

**Response (201):** `StaffMemberListSerializer` shape.

### `StaffMemberUpdateSerializer` (write)
Fields: `staff_role`, `is_valid`.  
Guard: cannot change the last Super Admin's role away from `super_admin`.

---

## 4. Users — `SellerProfileMobileSerializer` — `description`

### Old Response (partial)
```json
{ "id": 5, "nickname": "FalconBreeder", "country": "EG", ... }
```

### New Response (partial)
```json
{ "id": 5, "nickname": "FalconBreeder", "description": "Champion pigeon breeder.", "country": "EG", ... }
```

`description` is also accepted on PATCH.

---

## 5. Users — `StaffManagementViewSet`

**File:** `apps/users/views.py`  
**URL:** `admin/staff/` (registered in router)

| Method | URL | Who | Description |
|--------|-----|-----|-------------|
| `GET` | `/admin/staff/` | Super Admin, Admin | List all manager profiles |
| `POST` | `/admin/staff/` | Super Admin | Create a new staff member |
| `GET` | `/admin/staff/<pk>/` | Super Admin, Admin | Retrieve one staff member |
| `PATCH` | `/admin/staff/<pk>/` | Super Admin | Update role or validity |
| `DELETE` | `/admin/staff/<pk>/` | Super Admin | **Deactivates** (sets `is_active=False`, `is_valid=False`) — no hard delete |

All write actions are audit-logged.

---

## 6. Users — Admin ViewSet Changes

**File:** `apps/users/views.py`

### Permission change
`SellerProfileAdminViewSet` and `CustomerProfileAdminViewSet` switched from `permissions.IsAdminUser` to `HasAppAccess` with:
- `SellerProfileAdminViewSet.permission_resource = "SELLER_PROFILE_ADMIN"`
- `CustomerProfileAdminViewSet.permission_resource = "CUSTOMER_PROFILE_ADMIN"`

### Audit logging added
All bulk actions (`bulk_suspend`, `bulk_restore`, `bulk_activate`) and `destroy` now call `AuditLogService.log(...)`.

### New action: `GET /admin/sellers/<pk>/stats/`

Returns a summary of a seller's activity.

**Response:**
```json
{
  "birds_count": 12,
  "products_count": 5,
  "auctions_count": 3,
  "active_package": {
    "id": 2,
    "name": "Gold Package",
    "remaining_points": 80,
    "total_points": 100,
    "expires_at": "2026-09-01T00:00:00"
  },
  "badges": [
    { "badge_type": "first_purchase", "awarded_at": "2026-04-10T00:00:00" }
  ]
}
```

---

## 7. Users — Seller Mobile ViewSet Refactor

**File:** `apps/users/views.py` — `SellerProfileMobileViewSet`

### `create`
- Removed duplicate-profile check (now allowed — multiple seller rooms per user).
- No longer auto-sets `nickname` from username.
- Sets `activated=True` on creation.
- Success message changed: `"Room created successfully."` (was `"Seller profile created successfully"`).

### `get_queryset`
- Now returns **all** seller profiles for the user (`deleted__isnull=True`) — including inactive ones.
- Ordered by `created` ascending (was `-created`).

### `get_object`
- If a `pk` is in the URL → fetches that specific profile.
- If no `pk` → falls back to the currently active profile.

---

## 8. Authentication — `switch_user_profile` Refactor

**File:** `apps/authentication/services.py`

### Old behavior
- Blocked the switch if the user was already on that profile type (non-manager).
- Only deactivated + reactivated if role was different.

### New behavior
- If the user is **already** on that profile type (and not manager), returns `(False, None)` early.
- Deactivates all active profiles first, then activates the earliest-created profile of the target type.
- Works for managers too (they can switch between profiles of the same type).

### New function: `switch_user_profile_by_id(user, profile_id)`
Switches to a specific profile by PK — used for switching between multiple seller rooms.

---

## 9. Authentication — JWT Now Includes `staff_role`

**File:** `apps/authentication/services.py` — `build_token_for_user_role`

For manager profiles, the JWT token now includes `staff_role`.

### Old Token Payload (manager)
```json
{ "profile": "Manager", "user_id": 1, "is_activated": true }
```

### New Token Payload (manager)
```json
{ "profile": "Manager", "user_id": 1, "is_activated": true, "staff_role": "moderator" }
```

---

## 10. Authentication — `SwitchProfileAPIView` Accepts `profile_id`

**File:** `apps/authentication/views.py`

### Old Request
```json
{ "new_profile": "Seller" }
```

### New Request (by type — unchanged)
```json
{ "new_profile": "Seller" }
```

### New Request (by ID — new)
```json
{ "profile_id": 7 }
```

`profile_id` takes precedence when both are provided. Returns `400` if `profile_id` is non-integer.

---

## 11. Authentication — `ManagerRegisterAPIView` Now Protected

**File:** `apps/authentication/views.py`

### Old behavior
No authentication required (`permission_classes = []`).

### New behavior
Requires `HasStaffRole` with `required_staff_roles = [StaffRole.SUPER_ADMIN]`.  
Only a Super Admin can create new manager accounts via this endpoint.

---

## 12. General — `StaffPermissions` & `HasStaffRole`

**File:** `apps/general/authorizations.py`

### `StaffPermissions.can_access(staff_role, resource, action)`
Pure helper. Returns `True` if the role's entry in `STAFF_ROLE_PERMISSIONS` permits the given resource/action.  
Special case: `admin` is `__all__` but `destroy` is always blocked.

### `AuthorizationService` — Manager branch updated
Previously all managers passed unconditionally. Now fetches the `ManagerProfile.staff_role` and runs it through `StaffPermissions.can_access()`.

### New permission class: `HasStaffRole`
```python
permission_classes = [HasStaffRole]
required_staff_roles = [StaffRole.SUPER_ADMIN]          # enforced on writes
read_staff_roles    = [StaffRole.SUPER_ADMIN, StaffRole.ADMIN]  # enforced on SAFE_METHODS (optional)
```
Returns `403` for non-manager users, deleted profiles, or roles not in the allowed list.

---

## 13. General — `AuditLog` Model

**File:** `apps/general/models.py`

| Field | Type | Notes |
|-------|------|-------|
| `actor` | FK → `UserAccount` | SET_NULL on delete |
| `actor_staff_role` | CharField(30) | Snapshot of role at time of action |
| `action` | CharField(100) | e.g. `bulk_suspend`, `destroy`, `create_staff` |
| `resource_type` | CharField(100) | e.g. `SellerProfile`, `ManagerProfile` |
| `resource_id` | CharField(100) | PK of affected object (blank if N/A) |
| `details` | JSONField | Arbitrary context |
| `ip_address` | GenericIPAddressField | Extracted from `X-Forwarded-For` or `REMOTE_ADDR` |

Ordered by `-created`.

---

## 14. General — `AuditLogService`

**File:** `apps/general/services.py` (new untracked file)

```python
AuditLogService.log(request, action, resource_type, resource_id=None, details=None)
```

- Resolves `actor`, `actor_staff_role`, and IP automatically from the request.
- **Never raises** — any failure is swallowed and logged at ERROR level.

---

## 15. General — Audit Log Endpoint

**Files:** `apps/general/views.py`, `apps/general/urls.py` (new untracked files), `ppw/urls.py`

### New Endpoint
```
GET /api/audit-logs/
```

**Permission:** `HasStaffRole` — Super Admin and Admin only.

**Filter params:**

| Param | Description |
|-------|-------------|
| `actor` | Filter by actor user ID |
| `action` | Filter by action string |
| `resource_type` | e.g. `SellerProfile` |
| `resource_id` | PK of affected object |
| `actor_staff_role` | e.g. `moderator` |
| `date_after` | `YYYY-MM-DD` — created on or after |
| `date_before` | `YYYY-MM-DD` — created on or before |

**Response:**
```json
{
  "results": [
    {
      "id": 1,
      "actor": 2,
      "actor_username": "admin01",
      "actor_staff_role": "admin",
      "action": "bulk_suspend",
      "resource_type": "SellerProfile",
      "resource_id": "",
      "details": { "ids": [3, 5], "updated": 2 },
      "ip_address": "192.168.1.1",
      "created": "2026-06-20T10:00:00Z"
    }
  ],
  "count": 1,
  "next": null,
  "previous": null
}
```

---

## 16. Feed — `SellerListSerializer` additions

**File:** `apps/feed/serializers.py`

`user_id` and `description` added to `SellerListSerializer` (used by `GET /feed/sellers/`).

### Old Response Item
```json
{ "id": 7, "nickname": "FalconBreeder", "country": "EG", "avg_rating": "4.50", "ratings_count": 23, "avatar_url": "..." }
```

### New Response Item
```json
{ "id": 7, "user_id": 14, "nickname": "FalconBreeder", "description": "Champion pigeon breeder.", "country": "EG", "avg_rating": "4.50", "ratings_count": 23, "avatar_url": "..." }
```

---

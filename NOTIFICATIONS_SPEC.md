# Notifications Module — Backend Spec

## Overview

In-app notification centre for customer and seller profiles, plus full CRUD for managers.
Notifications are created programmatically by other modules (orders, auctions, payments, chat, loyalty).
No push-delivery (FCM/APNs) in this phase — REST polling only.

Mobile client hits `/api/core/notifications/` which routes through `apps.core`.
The `apps/core/` app currently has no files — this module populates it.

---

## Model — `Notification`

```python
# apps/core/models.py

class NotificationKind(models.TextChoices):
    ORDER         = "order",         "Order update"
    AUCTION_WON   = "auction_won",   "Auction won"
    NEW_BID       = "new_bid",        "New bid on item"
    BID_ACCEPTED  = "bid_accepted",   "Bid accepted"
    NEW_BADGE     = "new_badge",      "Badge awarded"
    PAYMENT       = "payment",        "Payment update"
    CHAT          = "chat",           "New message"

class Notification(TimeStampedModel):
    profile    = models.ForeignKey("users.Profile",   on_delete=models.CASCADE, related_name="notifications")
    kind       = models.CharField(max_length=30, choices=NotificationKind.choices)
    title      = models.CharField(max_length=200)
    body       = models.TextField(blank=True, default="")
    is_read    = models.BooleanField(default=False, db_index=True)
    read_at    = models.DateTimeField(null=True, blank=True)
    # optional JSON linking back to the source object
    metadata   = models.JSONField(default=dict, blank=True)
    # profile_type hint for mobile tab filter ("Customer" | "Seller" | "All")
    profile_type = models.CharField(max_length=20, blank=True, default="All")

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["profile", "is_read"]),
        ]
```

**Notes:**
- `profile` is the recipient. One record per recipient per event (no fan-out table).
- `metadata` stores source IDs, e.g. `{"order_id": 42}`, `{"auction_id": 7, "item_id": 3}`.
- `profile_type` tells the mobile which tab to show it in (`"Customer"`, `"Seller"`, or `"All"`).
- `TimeStampedModel` supplies `created` and `modified`.

---

## Service — `NotificationService`

```python
# apps/core/services.py

class NotificationService:

    @staticmethod
    def create(
        profile,
        kind: str,
        title: str,
        body: str = "",
        metadata: dict = None,
        profile_type: str = "All",
    ) -> Notification:
        return Notification.objects.create(
            profile=profile,
            kind=kind,
            title=title,
            body=body,
            metadata=metadata or {},
            profile_type=profile_type,
        )

    @staticmethod
    def mark_read(notification_id: int, profile) -> Notification:
        n = Notification.objects.get(pk=notification_id, profile=profile)
        if not n.is_read:
            n.is_read = True
            n.read_at = now()
            n.save(update_fields=["is_read", "read_at"])
        return n

    @staticmethod
    def unread_count(profile) -> int:
        return Notification.objects.filter(profile=profile, is_read=False).count()
```

---

## Serializers

```python
# apps/core/serializers.py

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "kind",
            "title",
            "body",
            "is_read",
            "read_at",
            "metadata",
            "profile_type",
            "created",
        ]
        read_only_fields = fields

class NotificationCreateSerializer(serializers.Serializer):
    """Manager-only manual creation."""
    profile_id   = serializers.IntegerField()
    kind         = serializers.ChoiceField(choices=NotificationKind.choices)
    title        = serializers.CharField(max_length=200)
    body         = serializers.CharField(allow_blank=True, default="")
    metadata     = serializers.JSONField(required=False, default=dict)
    profile_type = serializers.ChoiceField(
        choices=["Customer", "Seller", "All"], default="All"
    )

class UnreadCountSerializer(serializers.Serializer):
    count = serializers.IntegerField(read_only=True)
```

---

## API Endpoints

### Customer / Seller

| Method | URL | Action | Description |
|--------|-----|--------|-------------|
| `GET`  | `/api/core/notifications/` | `list` | Own profile notifications, newest first. Supports `?is_read=true/false` filter. |
| `POST` | `/api/core/notifications/<id>/read/` | `mark_read` | Mark one notification as read. Idempotent. |
| `GET`  | `/api/core/notifications/unread-count/` | `unread_count` | Returns `{"count": N}`. |

**Behaviour:**
- List is scoped to `request.user.active_profile` — no cross-profile leakage.
- `mark_read` returns the updated `NotificationSerializer`.
- Pagination: standard DRF page pagination (matches mobile's `results` key handling).
- Filter `?is_read=false` → unread only; `?is_read=true` → read only; omit → all.

### Manager

| Method | URL | Action | Description |
|--------|-----|--------|-------------|
| `GET`  | `/api/core/notifications/` | `list` | All notifications. Supports `?profile_id=X`, `?kind=order`, `?is_read=false`. |
| `POST` | `/api/core/notifications/` | `create` | Manually create a notification for any profile. |
| `DELETE` | `/api/core/notifications/<id>/` | `destroy` | Hard-delete a notification record. |

---

## Permission Matrix

Add to `apps/general/constants.py`:

```python
# In SELLER_PERMISSIONS:
"NOTIFICATION": ["list", "mark_read", "unread_count"],

# In CUSTOMER_PERMISSIONS:
"NOTIFICATION": ["list", "mark_read", "unread_count"],
```

Managers bypass the matrix (always `True`).

---

## Views

```python
# apps/core/views.py

class NotificationViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "NOTIFICATION"
    queryset = Notification.objects.all()

    def get_queryset(self):
        if request.user.role == ProfileType.MANAGER:
            qs = Notification.objects.select_related("profile").all()
            profile_id = self.request.query_params.get("profile_id")
            kind       = self.request.query_params.get("kind")
            is_read    = self.request.query_params.get("is_read")
            if profile_id: qs = qs.filter(profile_id=profile_id)
            if kind:       qs = qs.filter(kind=kind)
            if is_read is not None:
                qs = qs.filter(is_read=(is_read.lower() == "true"))
            return qs
        qs = Notification.objects.filter(profile=self.request.user.active_profile)
        is_read = self.request.query_params.get("is_read")
        if is_read is not None:
            qs = qs.filter(is_read=(is_read.lower() == "true"))
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return NotificationCreateSerializer
        return NotificationSerializer

    def list(self, request, *args, **kwargs):          # GET /
        qs = self.get_queryset()
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(
                NotificationSerializer(page, many=True).data
            )
        return Response(NotificationSerializer(qs, many=True).data)

    def create(self, request, *args, **kwargs):        # POST /  (manager only)
        serializer = NotificationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        n = NotificationService.create(**serializer.validated_data)
        return Response(NotificationSerializer(n).data, status=201)

    def mark_read(self, request, pk=None, **kwargs):   # POST /<id>/read/
        n = NotificationService.mark_read(pk, request.user.active_profile)
        return Response(NotificationSerializer(n).data)

    def unread_count(self, request, **kwargs):         # GET /unread-count/
        count = NotificationService.unread_count(request.user.active_profile)
        return Response({"count": count})

    def destroy(self, request, pk=None, **kwargs):     # DELETE /<id>/  (manager only)
        self.get_object().delete()
        return Response(status=204)
```

---

## URLs

```python
# apps/core/urls.py

from django.urls import path
from apps.core.views import NotificationViewSet

LIST_CREATE  = {"get": "list",  "post": "create"}
MARK_READ    = {"post": "mark_read"}
UNREAD_COUNT = {"get": "unread_count"}
DESTROY      = {"delete": "destroy"}

app_name = "core"

urlpatterns = [
    path("notifications/",                  NotificationViewSet.as_view(LIST_CREATE),  name="notification-list"),
    path("notifications/unread-count/",     NotificationViewSet.as_view(UNREAD_COUNT), name="notification-unread-count"),
    path("notifications/<int:pk>/read/",    NotificationViewSet.as_view(MARK_READ),    name="notification-mark-read"),
    path("notifications/<int:pk>/",         NotificationViewSet.as_view(DESTROY),      name="notification-detail"),
]
```

Register in `ppw/urls.py`:

```python
path("api/core/", include("apps.core.urls")),
```

---

## Integration Points (where `NotificationService.create()` gets called)

These are the trigger sites. Wire them once the model exists.

| Trigger | File | Recipient | kind | profile_type |
|---------|------|-----------|------|--------------|
| Order created (customer checkout) | `apps/orders/services.py` — `CheckoutService` | seller of each item | `order` | `Seller` |
| Order item approved by seller | `apps/orders/services.py` — `approve_item()` | customer | `order` | `Customer` |
| Order item rejected by seller | `apps/orders/services.py` — `reject_item()` | customer | `order` | `Customer` |
| Order item auto-cancelled (timeout) | `apps/orders/tasks.py` | customer | `order` | `Customer` |
| New bid placed on auction item | `apps/auctions/services.py` — `place_bid()` | auction owner (seller) | `new_bid` | `Seller` |
| Auction won (buy-now or end) | `apps/auctions/services.py` | winning customer | `auction_won` | `Customer` |
| Bid accepted | `apps/auctions/services.py` | bidder (customer) | `bid_accepted` | `Customer` |
| Payment request approved | `apps/payments/services.py` | customer | `payment` | `Customer` |
| Payment request rejected | `apps/payments/services.py` | customer | `payment` | `Customer` |
| New chat message received | `apps/chat/tasks.py` — `send_chat_message_notification()` | message receiver profile | `chat` | depends on receiver type |
| Badge awarded | `apps/loyalty/services.py` — `BadgeService.award_badge()` | awarded profile | `new_badge` | depends on profile type |

---

## Also Fix (same session)

`apps/general/constants.py` — `PEDIGREE_DOCUMENT` is missing `"list"` for both seller and customer.
We added `GET /pedigrees/documents/` last session but never updated the permission matrix, so it will always return 403.

```python
# Add "list" to PEDIGREE_DOCUMENT in both SELLER_PERMISSIONS and CUSTOMER_PERMISSIONS
"PEDIGREE_DOCUMENT": ["list", "create", "retrieve", "partial_update"],
```

---

## Migration

```bash
python manage.py makemigrations core
python manage.py migrate
```

Model depends only on `users.Profile` (existing) and `TimeStampedModel` from `apps.general` or `django-model-utils`.
No circular imports risk.

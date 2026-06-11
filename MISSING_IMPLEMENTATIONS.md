# PPW — Missing Implementations (from PPW-master)

This file is the complete implementation guide for everything that exists in
PPW-master but is absent from the current PPW codebase.
Each section names the file, its purpose, and the exact implementation
required. Work through the sections in order — later sections depend on
earlier ones (especially `apps/pigeon_core` which is imported by
`seller_home.py`).

---

## Overview of changes

| # | File | Type | Action |
|---|------|------|--------|
| 1 | `apps/authentication/services.py` | Existing | Modify |
| 2 | `apps/pigeon_core/__init__.py` | New app | Create |
| 3 | `apps/pigeon_core/apps.py` | New app | Create |
| 4 | `apps/pigeon_core/models.py` | New app | Create |
| 5 | `apps/pigeon_core/services.py` | New app | Create |
| 6 | `apps/pigeon_core/serializers.py` | New app | Create |
| 7 | `apps/pigeon_core/views.py` | New app | Create |
| 8 | `apps/pigeon_core/urls.py` | New app | Create |
| 9 | `apps/pigeon_core/admin.py` | New app | Create |
| 10 | `apps/pigeon_core/signals.py` | New app | Create |
| 11 | `apps/pigeon_core/migrations/0001_initial.py` | New app | Create |
| 12 | `apps/pigeon_core/migrations/0002_seed_subscription_packages.py` | New app | Create |
| 13 | `apps/users/seller_home.py` | New file | Create |
| 14 | `apps/users/customer_home.py` | New file | Create |
| 15 | `apps/users/views.py` | Existing | Modify |
| 16 | `ppw/seed_view.py` | New file | Create |
| 17 | `ppw/urls.py` | Existing | Modify |
| 18 | `ppw/settings/base.py` | Existing | Modify |
| 19 | `tests/authentication/test_profile_switching.py` | Existing | Modify |

---

## 1. `apps/authentication/services.py` — Modify

**Purpose:** Fix a live bug where `POST /api/auth/switch-profile/` returns 400
for seller-only accounts (no `CustomerProfile` row) or customer-only accounts
(no `SellerProfile` row). When the target profile row is missing, auto-create
it with safe defaults instead of failing.

**Current file has:** `switch_user_profile`, `build_token_for_user`,
`build_token_for_user_role` — no auto-create logic.

**Required changes:**

Add two private helpers before `switch_user_profile`. Update
`switch_user_profile` to call them when the target profile row is not found.
Also add the missing `ValidationError` import and the `CustomerProfile` /
`SellerProfile` imports.

**Full replacement of the file:**

```python
from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, Profile, SellerProfile


def _ensure_customer_profile(user) -> Profile | None:
    existing = Profile.objects.filter(
        user=user, type=ProfileType.CUSTOMER
    ).first()
    if existing:
        return existing
    seller = SellerProfile.objects.filter(user=user).first()
    if not seller:
        return None
    return CustomerProfile.objects.create(
        user=user,
        country=seller.country,
        currency=seller.currency,
        is_active=False,
        activated=True,
    )


def _ensure_seller_profile(user) -> Profile | None:
    existing = Profile.objects.filter(
        user=user, type=ProfileType.SELLER
    ).first()
    if existing:
        return existing
    customer = CustomerProfile.objects.filter(user=user).first()
    if not customer:
        return None
    return SellerProfile.objects.create(
        user=user,
        country=customer.country,
        currency=customer.currency,
        is_active=False,
        activated=True,
        nickname=(user.username or "")[:150],
    )


def switch_user_profile(user, new_profile_type: str) -> tuple[bool, str]:
    with transaction.atomic():
        if user.role != new_profile_type and user.role != ProfileType.MANAGER:
            Profile.objects.filter(user=user, is_active=True).update(
                is_active=False
            )
            profile = Profile.objects.filter(
                user=user, type=new_profile_type
            ).first()
            if not profile:
                if new_profile_type == ProfileType.CUSTOMER:
                    profile = _ensure_customer_profile(user)
                elif new_profile_type == ProfileType.SELLER:
                    profile = _ensure_seller_profile(user)
            if profile:
                profile.is_active = True
                profile.save(update_fields=["is_active"])
                return True, profile.type
            else:
                transaction.set_rollback(True)
                return False, None
        else:
            return False, None


def build_token_for_user(user):
    profile = Profile.objects.filter(user=user, is_active=True).first()
    if not profile:
        raise ValidationError("No active profile found for this user.")
    return build_token_for_user_role(user, profile)


def build_token_for_user_role(user, profile):
    token = RefreshToken.for_user(user)
    token["profile"] = profile.type
    token["user_id"] = user.id
    token["is_activated"] = profile.activated
    return token
```

**Key behavioural differences from the current version:**
- `build_token_for_user` now does an explicit DB query for the active profile
  and raises `ValidationError` when none exists, instead of calling
  `user.active_profile` which raises `PermissionDenied`.
- `switch_user_profile` calls `_ensure_customer_profile` /
  `_ensure_seller_profile` before giving up, so seller-only or customer-only
  accounts can switch without 400.

---

## 2. `apps/pigeon_core/__init__.py` — Create

**Purpose:** Marks the directory as a Python package.

```python
```
_(empty file)_

---

## 3. `apps/pigeon_core/apps.py` — Create

**Purpose:** Django `AppConfig`. Registers the app label `apps.pigeon_core`
and imports signals when Django starts so the signal receivers wire up.

```python
from django.apps import AppConfig


class PigeonCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pigeon_core"
    verbose_name = "Pigeon core (notifications, activity, points, subscriptions)"

    def ready(self):
        import apps.pigeon_core.signals  # noqa: F401
```

---

## 4. `apps/pigeon_core/models.py` — Create

**Purpose:** Six models that cover in-app notifications, an append-only
activity audit log, a per-profile points wallet + transaction ledger, and
seller subscription packages.

All models inherit `BaseModel` from `apps.general.models` (soft-delete +
timestamps).

### Models

**`InAppNotification`**
Persisted notification row per user. `profile_type` scopes it to a specific
profile mode (`Customer`, `Seller`) or `All`. `kind` is a short string tag
(e.g. `"order_pending"`, `"new_bid"`). `read_at` is null until the user marks
it read.

**`ActivityLogEntry`**
Append-only audit line. Stores which user + profile mode performed an
`action` string with a human-readable `summary` and a `metadata` JSON blob.
Never updated after creation.

**`PointWallet`**
One wallet per `Profile` (OneToOne). Holds a non-negative integer `balance`.
Created lazily by `ensure_point_wallet()` in services.

**`PointTransaction`**
Each row records a single delta (positive = earn, negative = spend/expire)
against a `PointWallet`. Stores `reason`, optional `ref_type`/`ref_id` for
traceability. Immutable after creation.

**`SubscriptionPackage`**
Manager-created tier definition. Fields: `slug` (unique), `name`,
`description`, `price` (decimal), `currency`, `commission_percent`,
`sort_order`, `is_active`. Not seller-owned — it is a catalogue entry.

**`UserSubscription`**
Links a `UserAccount` to a `SubscriptionPackage`. Tracks `starts_at`,
optional `expires_at`, and `is_active`. A user can have at most one
`is_active=True` subscription at a time (enforced in service layer, not a DB
constraint yet).

```python
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.general.models import BaseModel
from apps.users.constants import ProfileType


class InAppNotification(BaseModel):

    class Audience(models.TextChoices):
        CUSTOMER = ProfileType.CUSTOMER.value, "Customer"
        SELLER = ProfileType.SELLER.value, "Seller"
        ALL = "All", "All"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="in_app_notifications",
        db_index=True,
    )
    profile_type = models.CharField(
        max_length=20,
        choices=Audience.choices,
        default=Audience.ALL,
        db_index=True,
    )
    kind = models.CharField(max_length=64, db_index=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created",)
        indexes = [
            models.Index(fields=("user", "profile_type", "created")),
        ]


class ActivityLogEntry(BaseModel):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="activity_entries",
        db_index=True,
    )
    profile_type = models.CharField(
        max_length=20,
        choices=ProfileType.choices,
        db_index=True,
    )
    action = models.CharField(max_length=64, db_index=True)
    summary = models.CharField(max_length=512)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created",)
        indexes = [
            models.Index(fields=("user", "profile_type", "created")),
        ]


class PointWallet(BaseModel):

    profile = models.OneToOneField(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="point_wallet",
    )
    balance = models.IntegerField(default=0, validators=[MinValueValidator(0)])

    def __str__(self):
        return f"Wallet profile={self.profile_id} balance={self.balance}"


class PointTransaction(BaseModel):

    wallet = models.ForeignKey(
        PointWallet,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    delta = models.IntegerField()
    reason = models.CharField(max_length=64)
    ref_type = models.CharField(max_length=64, blank=True)
    ref_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("-created",)


class SubscriptionPackage(BaseModel):

    slug = models.SlugField(unique=True, max_length=64)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    currency = models.CharField(max_length=3, default="EGP")
    commission_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "id")


class UserSubscription(BaseModel):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    package = models.ForeignKey(
        SubscriptionPackage,
        on_delete=models.PROTECT,
        related_name="user_subscriptions",
    )
    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=("user", "is_active")),
        ]
```

---

## 5. `apps/pigeon_core/services.py` — Create

**Purpose:** Single service function: `ensure_point_wallet(profile)`.
Gets or creates the `PointWallet` for a `Profile` inside an atomic block with
a `select_for_update` lock to prevent duplicate wallet creation under
concurrent requests.

```python
from django.db import transaction

from apps.pigeon_core.models import PointWallet
from apps.users.models import Profile


def ensure_point_wallet(profile: Profile) -> PointWallet:
    with transaction.atomic():
        w, _ = PointWallet.objects.select_for_update().get_or_create(
            profile=profile,
            defaults={"balance": 0},
        )
        return w
```

---

## 6. `apps/pigeon_core/serializers.py` — Create

**Purpose:** Read-only serializers for the four viewsets. All fields are
`read_only_fields` — these models are written by services and signals, never
by direct API input.

```python
from rest_framework import serializers

from apps.pigeon_core.models import (
    ActivityLogEntry,
    InAppNotification,
    PointTransaction,
    SubscriptionPackage,
)


class InAppNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = InAppNotification
        fields = (
            "id", "profile_type", "kind", "title",
            "body", "payload", "read_at", "created",
        )
        read_only_fields = fields


class ActivityLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityLogEntry
        fields = (
            "id", "profile_type", "action",
            "summary", "metadata", "created",
        )
        read_only_fields = fields


class PointTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PointTransaction
        fields = ("id", "delta", "reason", "ref_type", "ref_id", "created")
        read_only_fields = fields


class SubscriptionPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPackage
        fields = (
            "id", "slug", "name", "description", "price",
            "currency", "commission_percent", "sort_order",
            "is_active", "created",
        )
        read_only_fields = fields
```

---

## 7. `apps/pigeon_core/views.py` — Create

**Purpose:** Four viewsets exposing read-only access (plus mark-read actions
for notifications).

### `InAppNotificationViewSet`
- Inherits `ReadOnlyModelViewSet`.
- `get_queryset`: scopes to `request.user`, filters by `profile_type = ALL`
  OR current active profile type.
- Custom action `GET notifications/unread-count/` → returns `{"count": N}`.
- Custom action `POST notifications/<id>/read/` → sets `read_at = now()` if
  not already set, returns the notification.

### `ActivityLogViewSet`
- Inherits `ReadOnlyModelViewSet`.
- `get_queryset`: scopes to `request.user`. Accepts optional `?profile=`
  query param (`Customer` or `Seller`) to further filter.

### `PointWalletViewSet`
- Inherits `ViewSet` (not a ModelViewSet — no list of wallets, just current
  user's balance).
- `list` action: calls `ensure_point_wallet(active_profile)`, returns
  `{"balance": N, "profile_type": "..."}`.
- Custom action `GET points/history/` → returns last 100 `PointTransaction`
  rows for that wallet.

### `SubscriptionPackageViewSet`
- Inherits `ReadOnlyModelViewSet`.
- `queryset`: only `is_active=True` packages, ordered by `sort_order`, `id`.

```python
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.pigeon_core.models import (
    ActivityLogEntry,
    InAppNotification,
    PointTransaction,
    PointWallet,
    SubscriptionPackage,
)
from apps.pigeon_core.serializers import (
    ActivityLogSerializer,
    InAppNotificationSerializer,
    PointTransactionSerializer,
    SubscriptionPackageSerializer,
)
from apps.pigeon_core.services import ensure_point_wallet
from apps.users.constants import ProfileType


class InAppNotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = InAppNotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        profile = getattr(user, "active_profile", None)
        if not profile:
            return InAppNotification.objects.none()
        mode = profile.type
        return InAppNotification.objects.filter(user=user).filter(
            Q(profile_type=InAppNotification.Audience.ALL)
            | Q(profile_type=mode)
        )

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        profile = getattr(request.user, "active_profile", None)
        if not profile:
            return Response({"count": 0})
        qs = self.get_queryset().filter(read_at__isnull=True)
        return Response({"count": qs.count()})

    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        obj = self.get_object()
        if obj.user_id != request.user.id:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if obj.read_at is None:
            obj.read_at = timezone.now()
            obj.save(update_fields=["read_at"])
        return Response(InAppNotificationSerializer(obj).data)


class ActivityLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ActivityLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        profile_filter = self.request.query_params.get("profile")
        qs = ActivityLogEntry.objects.filter(user=user)
        if profile_filter in (ProfileType.CUSTOMER, ProfileType.SELLER):
            qs = qs.filter(profile_type=profile_filter)
        return qs


class PointWalletViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        profile = getattr(request.user, "active_profile", None)
        if not profile:
            return Response(
                {"detail": "No active profile."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        wallet = ensure_point_wallet(profile)
        return Response({"balance": wallet.balance, "profile_type": profile.type})

    @action(detail=False, methods=["get"], url_path="history")
    def history(self, request):
        profile = getattr(request.user, "active_profile", None)
        if not profile:
            return Response(
                {"detail": "No active profile."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        wallet = ensure_point_wallet(profile)
        txs = PointTransaction.objects.filter(wallet=wallet).order_by("-created")[:100]
        return Response(PointTransactionSerializer(txs, many=True).data)


class SubscriptionPackageViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SubscriptionPackage.objects.filter(is_active=True).order_by(
        "sort_order", "id"
    )
    serializer_class = SubscriptionPackageSerializer
    permission_classes = [permissions.IsAuthenticated]
```

---

## 8. `apps/pigeon_core/urls.py` — Create

**Purpose:** DRF `DefaultRouter` wiring the four viewsets to their URL
prefixes. This file is mounted at `api/core/` in the root `urls.py`.

Resulting endpoints:
- `GET  /api/core/notifications/`
- `GET  /api/core/notifications/<id>/`
- `GET  /api/core/notifications/unread-count/`
- `POST /api/core/notifications/<id>/read/`
- `GET  /api/core/activity/`
- `GET  /api/core/activity/<id>/`
- `GET  /api/core/points/`
- `GET  /api/core/points/history/`
- `GET  /api/core/subscription-packages/`
- `GET  /api/core/subscription-packages/<id>/`

```python
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.pigeon_core.views import (
    ActivityLogViewSet,
    InAppNotificationViewSet,
    PointWalletViewSet,
    SubscriptionPackageViewSet,
)

router = DefaultRouter()
router.register("notifications", InAppNotificationViewSet, basename="in-app-notifications")
router.register("activity", ActivityLogViewSet, basename="activity-log")
router.register("points", PointWalletViewSet, basename="points-wallet")
router.register("subscription-packages", SubscriptionPackageViewSet, basename="subscription-packages")

urlpatterns = [
    path("", include(router.urls)),
]
```

---

## 9. `apps/pigeon_core/admin.py` — Create

**Purpose:** Registers all six models in the Django admin so managers can
inspect notifications, activity, wallets, transactions, packages, and
subscriptions via the admin interface.

```python
from django.contrib import admin

from apps.pigeon_core.models import (
    ActivityLogEntry,
    InAppNotification,
    PointTransaction,
    PointWallet,
    SubscriptionPackage,
    UserSubscription,
)


@admin.register(InAppNotification)
class InAppNotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "profile_type", "kind", "read_at", "created")
    list_filter = ("profile_type", "kind")


@admin.register(ActivityLogEntry)
class ActivityLogEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "profile_type", "action", "created")


@admin.register(PointWallet)
class PointWalletAdmin(admin.ModelAdmin):
    list_display = ("id", "profile", "balance")


@admin.register(PointTransaction)
class PointTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "wallet", "delta", "reason", "created")


@admin.register(SubscriptionPackage)
class SubscriptionPackageAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "price", "currency", "is_active", "sort_order")


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "package", "is_active", "starts_at", "expires_at")
```

---

## 10. `apps/pigeon_core/signals.py` — Create

**Purpose:** Django signal receivers that automatically create
`InAppNotification` and `ActivityLogEntry` rows when important domain events
occur, without requiring explicit service calls in every app.

Two receivers:

**`order_item_notify_seller`** — fires on `OrderItem` post-save (created
only). If the new item's status is `PENDING_SELLER_APPROVAL`, creates a
`Seller`-scoped notification and activity log entry for the seller's user.

**`bid_notify_seller`** — fires on `Bid` post-save (created only). Creates a
`Seller`-scoped notification and activity entry for the auction owner's user.
Skips if the bidder is the seller themselves.

```python
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.auctions.models import Bid
from apps.orders.models import OrderItem, OrderItemStatus
from apps.pigeon_core.models import ActivityLogEntry, InAppNotification
from apps.users.constants import ProfileType


def _notify_seller_user(*, seller_user, title, body, kind, payload, profile_type):
    InAppNotification.objects.create(
        user=seller_user,
        profile_type=profile_type,
        kind=kind,
        title=title,
        body=body,
        payload=payload or {},
    )


def _log_activity(*, user, profile_type, action, summary, metadata=None):
    ActivityLogEntry.objects.create(
        user=user,
        profile_type=profile_type,
        action=action,
        summary=summary,
        metadata=metadata or {},
    )


@receiver(post_save, sender=OrderItem)
def order_item_notify_seller(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.status != OrderItemStatus.PENDING_SELLER_APPROVAL:
        return
    seller = instance.seller
    user = seller.user
    body = f"بانتظار الموافقة — {instance.asset.title}"
    _notify_seller_user(
        seller_user=user,
        title="طلب جديد",
        body=body,
        kind="order_pending",
        payload={"order_item_id": instance.id},
        profile_type=ProfileType.SELLER,
    )
    _log_activity(
        user=user,
        profile_type=ProfileType.SELLER,
        action="order_item_pending",
        summary=body,
        metadata={"order_item_id": instance.id},
    )


@receiver(post_save, sender=Bid)
def bid_notify_seller(sender, instance, created, **kwargs):
    if not created:
        return
    item = instance.item
    auction = item.auction
    seller_profile = auction.owner
    seller_user = seller_profile.user
    if instance.bidder_id == seller_profile.id:
        return
    bird_name = item.bird.name if item.bird_id else "مزاد"
    body = f"على {bird_name} — {instance.amount}"
    _notify_seller_user(
        seller_user=seller_user,
        title="مزايدة جديدة",
        body=str(body),
        kind="new_bid",
        payload={"bid_id": instance.id, "item_id": item.id},
        profile_type=ProfileType.SELLER,
    )
    _log_activity(
        user=seller_user,
        profile_type=ProfileType.SELLER,
        action="bid_placed",
        summary=str(body),
        metadata={"bid_id": instance.id},
    )
```

**Important:** The signal file is imported only inside `PigeonCoreConfig.ready()`
(see `apps.py`). Do NOT import it at module level from any other file.

---

## 11. `apps/pigeon_core/migrations/0001_initial.py` — Create

**Purpose:** Initial migration creating all six `pigeon_core` tables.
Depends on `users.0003_alter_profile_activated` (the latest users migration).

```python
# Generated by Django 4.2.15

from django.conf import settings
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django_extensions.db.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('users', '0003_alter_profile_activated'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SubscriptionPackage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('slug', models.SlugField(max_length=64, unique=True)),
                ('name', models.CharField(max_length=128)),
                ('description', models.TextField(blank=True)),
                ('price', models.DecimalField(decimal_places=2, max_digits=10, validators=[django.core.validators.MinValueValidator(0)])),
                ('currency', models.CharField(default='EGP', max_length=3)),
                ('commission_percent', models.DecimalField(decimal_places=2, default=0, max_digits=5, validators=[django.core.validators.MinValueValidator(0)])),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ('sort_order', 'id')},
        ),
        migrations.CreateModel(
            name='PointWallet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('balance', models.IntegerField(default=0, validators=[django.core.validators.MinValueValidator(0)])),
                ('profile', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='point_wallet', to='users.profile')),
            ],
            options={'abstract': False},
        ),
        migrations.CreateModel(
            name='PointTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('delta', models.IntegerField()),
                ('reason', models.CharField(max_length=64)),
                ('ref_type', models.CharField(blank=True, max_length=64)),
                ('ref_id', models.PositiveIntegerField(blank=True, null=True)),
                ('wallet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transactions', to='pigeon_core.pointwallet')),
            ],
            options={'ordering': ('-created',)},
        ),
        migrations.CreateModel(
            name='UserSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('starts_at', models.DateTimeField()),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('package', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='user_subscriptions', to='pigeon_core.subscriptionpackage')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subscriptions', to=settings.AUTH_USER_MODEL)),
            ],
            options={'indexes': [models.Index(fields=['user', 'is_active'], name='pigeon_core_user_id_240616_idx')]},
        ),
        migrations.CreateModel(
            name='InAppNotification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('profile_type', models.CharField(choices=[('Customer', 'Customer'), ('Seller', 'Seller'), ('All', 'All')], db_index=True, default='All', max_length=20)),
                ('kind', models.CharField(db_index=True, max_length=64)),
                ('title', models.CharField(max_length=255)),
                ('body', models.TextField(blank=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='in_app_notifications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('-created',),
                'indexes': [models.Index(fields=['user', 'profile_type', 'created'], name='pigeon_core_user_id_c009f5_idx')],
            },
        ),
        migrations.CreateModel(
            name='ActivityLogEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('profile_type', models.CharField(choices=[('Seller', 'Seller'), ('Customer', 'Customer'), ('Manager', 'Manager')], db_index=True, max_length=20)),
                ('action', models.CharField(db_index=True, max_length=64)),
                ('summary', models.CharField(max_length=512)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='activity_entries', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('-created',),
                'indexes': [models.Index(fields=['user', 'profile_type', 'created'], name='pigeon_core_user_id_845fb6_idx')],
            },
        ),
    ]
```

**Note:** If the latest users migration in this repo has a different name than
`0003_alter_profile_activated`, update the dependency to match whatever the
latest users migration is before running `migrate`.

---

## 12. `apps/pigeon_core/migrations/0002_seed_subscription_packages.py` — Create

**Purpose:** Data migration that seeds three default `SubscriptionPackage`
rows on first run. It is a no-op if packages already exist.

```python
from decimal import Decimal
from django.db import migrations


def seed_packages(apps, schema_editor):
    SubscriptionPackage = apps.get_model("pigeon_core", "SubscriptionPackage")
    if SubscriptionPackage.objects.exists():
        return
    rows = [
        dict(slug="basic",  name="أساسية", description="باقة عمولة مع عربون",
             price=Decimal("0"),   currency="EGP", commission_percent=Decimal("10.00"),
             sort_order=0, is_active=True),
        dict(slug="pro",    name="Pro",    description="مزادات غير محدودة وميزات إضافية",
             price=Decimal("299"), currency="EGP", commission_percent=Decimal("7.00"),
             sort_order=1, is_active=True),
        dict(slug="elite",  name="Elite",  description="أعلى مستوى من الخدمات",
             price=Decimal("599"), currency="EGP", commission_percent=Decimal("5.00"),
             sort_order=2, is_active=True),
    ]
    for r in rows:
        SubscriptionPackage.objects.create(**r)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("pigeon_core", "0001_initial"),
    ]
    operations = [
        migrations.RunPython(seed_packages, noop_reverse),
    ]
```

---

## 13. `apps/users/seller_home.py` — Create

**Purpose:** Pure function `build_seller_home_payload(seller: SellerProfile) -> dict`
that aggregates all data needed for the Flutter seller home screen in one DB
round-trip burst. Called by the `home_summary` action on
`SellerProfileMobileViewSet`.

**What it computes:**

| Key | Source |
|-----|--------|
| `active_live_auctions` | `Auction` rows owned by seller, `ACTIVE`, `is_visible=True`, within `start_time`/`end_time` window |
| `my_active_listings` | `Auction` rows owned by seller, status `ACTIVE` (regardless of time window) |
| `sales_today` | `OrderItem` rows where `seller=seller`, `created >= start_of_day`, status in `SELLER_APPROVED` or `SETTLED` |
| `pending_order_items` | `OrderItem` rows where `seller=seller`, status `PENDING_SELLER_APPROVAL` |
| `birds_count` | `Bird` rows where `owner=seller` |
| `notifications` | Merged list (max 12) from: pending order items (kind `order`), recent bids on seller's auction items excluding self-bids (kind `bid`), recently sold auction items (kind `sold`). Sorted descending by `created_at`. Each entry has `kind`, `title`, `created_at` (ISO), `time_hint` (Arabic relative string), `is_new` (bool, within 24h). |
| `notifications_new_count` | Count of entries where `is_new=True` |
| `provider_notes` | List of structured hint tiles for the mobile UI. Always includes `auction_identity`, `identity_qr`, `video`. Prepends `activation` tile when `seller.activated` is `False`. |
| `nickname` | `seller.nickname` or `seller.user.username` |
| `balance` | `str(seller.balance)` |
| `currency` | `str(seller.currency)` |
| `profile_activated` | `seller.activated` |
| `subscription_tier` | Name of the seller's active `UserSubscription.package`, or `null` |
| `points_balance` | Balance from `ensure_point_wallet(seller)` |
| `referral` | Always `null` (reserved for future use) |

**Arabic time hint logic (`_ar_time_hint`):**
- < 60 s → `"الآن"`
- < 3600 s → `"منذ N دقيقة"` (singular: `"منذ دقيقة"`)
- < 86400 s → `"منذ N ساعة"` (singular: `"منذ ساعة"`)
- else → `"منذ N يوم"` (singular: `"منذ يوم"`)

**Note on `OrderItemStatus` values:** uses `SELLER_APPROVED` and `SETTLED`
(not `approved` / `completed` from `apps.orders` in the current PPW — confirm
the exact status string values match `apps.orders.models.OrderItemStatus` in
this codebase before deploying).

```python
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.assets.models import Bird
from apps.auctions.models import (
    Auction,
    AuctionItem,
    AuctionItemStatus,
    AuctionStatus,
    Bid,
)
from apps.orders.models import OrderItem, OrderItemStatus
from apps.users.models import SellerProfile


def _ar_time_hint(dt) -> str:
    now = timezone.now()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    delta = now - dt
    secs = int(max(0, delta.total_seconds()))
    if secs < 60:
        return "الآن"
    if secs < 3600:
        m = secs // 60
        return f"منذ {m} دقيقة" if m != 1 else "منذ دقيقة"
    if secs < 86400:
        h = secs // 3600
        return f"منذ {h} ساعة" if h != 1 else "منذ ساعة"
    d = secs // 86400
    return f"منذ {d} يوم" if d != 1 else "منذ يوم"


def build_seller_home_payload(seller: SellerProfile) -> dict:
    now = timezone.now()
    local_now = timezone.localtime(now)
    start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    active_live = Auction.objects.filter(
        owner=seller,
        status=AuctionStatus.ACTIVE,
        is_visible=True,
        start_time__lte=now,
        end_time__gt=now,
    ).count()

    my_active_listings = Auction.objects.filter(
        owner=seller, status=AuctionStatus.ACTIVE
    ).count()

    sales_today = OrderItem.objects.filter(
        seller=seller,
        created__gte=start_of_day,
        status__in=(
            OrderItemStatus.SELLER_APPROVED,
            OrderItemStatus.SETTLED,
        ),
    ).count()

    pending_orders = OrderItem.objects.filter(
        seller=seller,
        status=OrderItemStatus.PENDING_SELLER_APPROVAL,
    ).count()

    birds_count = Bird.objects.filter(owner=seller).count()
    visible_auctions = Auction.objects.filter(
        owner=seller, is_visible=True
    ).exists()

    notifications: list[dict] = []
    cutoff_new = now - timedelta(hours=24)

    pending_items = (
        OrderItem.objects.filter(
            seller=seller,
            status=OrderItemStatus.PENDING_SELLER_APPROVAL,
        )
        .select_related("asset")
        .order_by("-created")[:5]
    )
    for oi in pending_items:
        notifications.append({
            "kind": "order",
            "title": f"طلب جديد بانتظار الموافقة — {oi.asset.title}",
            "created_at": oi.created.isoformat(),
            "time_hint": _ar_time_hint(oi.created),
            "is_new": oi.created >= cutoff_new,
        })

    recent_bids = (
        Bid.objects.filter(item__auction__owner=seller)
        .exclude(bidder_id=seller.id)
        .select_related("item", "item__bird")
        .order_by("-created")[:8]
    )
    for b in recent_bids:
        bird = getattr(b.item, "bird", None)
        label = bird.name if bird is not None else "مزاد"
        notifications.append({
            "kind": "bid",
            "title": f'مزايدة جديدة على "{label}" — {b.amount} ج.م',
            "created_at": b.created.isoformat(),
            "time_hint": _ar_time_hint(b.created),
            "is_new": b.created >= cutoff_new,
        })

    sold_items = (
        AuctionItem.objects.filter(
            auction__owner=seller,
            status=AuctionItemStatus.SOLD,
        )
        .select_related("bird", "auction")
        .order_by("-modified")[:5]
    )
    for it in sold_items:
        bird = getattr(it, "bird", None)
        label = bird.name if bird is not None else it.auction.title
        notifications.append({
            "kind": "sold",
            "title": f'تم بيع "{label}"',
            "created_at": it.modified.isoformat(),
            "time_hint": _ar_time_hint(it.modified),
            "is_new": it.modified >= cutoff_new,
        })

    notifications.sort(key=lambda x: x["created_at"], reverse=True)
    notifications = notifications[:12]
    new_count = sum(1 for n in notifications if n["is_new"])

    provider_notes = [
        {
            "key": "auction_identity",
            "title": "إضافة مزاد مع هوية رقمية",
            "subtitle": f"حمامك المسجل: {birds_count} — انشر مزاداً مرئياً للمشترين",
            "accent": "primary",
            "done": visible_auctions and birds_count > 0,
        },
        {
            "key": "identity_qr",
            "title": "عرض الهوية الرقمية",
            "subtitle": "QR + معلومات الطير + رقم الدبلة من بيانات الأصول",
            "accent": "purple",
            "done": birds_count > 0,
        },
        {
            "key": "video",
            "title": "الفيديو الإلزامي (4 مراحل)",
            "subtitle": "يتم ربطه لاحقاً بنظام التحقق من الفيديو",
            "accent": "red",
            "done": False,
        },
    ]

    if not seller.activated:
        provider_notes.insert(0, {
            "key": "activation",
            "title": "تفعيل حساب البائع",
            "subtitle": "حسابك قيد المراجعة من الإدارة",
            "accent": "orange",
            "done": False,
        })

    from apps.pigeon_core.models import UserSubscription
    from apps.pigeon_core.services import ensure_point_wallet

    wallet = ensure_point_wallet(seller)
    points_balance = wallet.balance
    sub = (
        UserSubscription.objects.filter(user=seller.user, is_active=True)
        .select_related("package")
        .order_by("-starts_at")
        .first()
    )
    subscription_tier = sub.package.name if sub and sub.package_id else None

    return {
        "nickname": seller.nickname or seller.user.username or "",
        "balance": str(seller.balance),
        "currency": str(seller.currency),
        "subscription_tier": subscription_tier,
        "points_balance": points_balance,
        "profile_activated": seller.activated,
        "active_live_auctions": active_live,
        "my_active_listings": my_active_listings,
        "sales_today": sales_today,
        "pending_order_items": pending_orders,
        "notifications": notifications,
        "notifications_new_count": new_count,
        "referral": None,
        "provider_notes": provider_notes,
    }
```

**Status value check required before deploying:** `OrderItemStatus.SELLER_APPROVED`
and `OrderItemStatus.SETTLED` must match the exact choice values in
`apps/orders/models.py`. In the current PPW the equivalent statuses may be
named differently (e.g. `approved`, `completed`). Update the `status__in`
filter to match.

---

## 14. `apps/users/customer_home.py` — Create

**Purpose:** Pure function `build_customer_home_payload(customer: CustomerProfile) -> dict`
aggregating data for the Flutter customer home screen.

**What it computes:**

| Key | Source |
|-----|--------|
| `open_orders` | `Order` rows where `owner=customer`, status in `OPEN` or `PARTIALLY_SETTLED` |
| `pending_order_items` | `OrderItem` rows where `order__owner=customer`, status in `PENDING_SELLER_APPROVAL` or `SELLER_APPROVED` |
| `notifications` | Last 10 `OrderItem` rows for the customer, each as a feed entry with `kind="order_item"`, Arabic title, ISO timestamp, Arabic time hint, `is_new` bool |
| `notifications_new_count` | Count of `is_new=True` entries |
| `nickname` | `customer.user.username` |
| `balance` | `str(customer.balance)` |
| `currency` | `str(customer.currency)` |

**Note:** `OrderStatus.OPEN` and `OrderStatus.PARTIALLY_SETTLED` must match
the exact values in `apps/orders/models.py`. Confirm before deploying.

```python
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.orders.models import Order, OrderItem, OrderItemStatus, OrderStatus
from apps.users.models import CustomerProfile


def _ar_time_hint(dt) -> str:
    now = timezone.now()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    delta = now - dt
    secs = int(max(0, delta.total_seconds()))
    if secs < 60:
        return "الآن"
    if secs < 3600:
        m = secs // 60
        return f"منذ {m} دقيقة" if m != 1 else "منذ دقيقة"
    if secs < 86400:
        h = secs // 3600
        return f"منذ {h} ساعة" if h != 1 else "منذ ساعة"
    d = secs // 86400
    return f"منذ {d} يوم" if d != 1 else "منذ يوم"


def build_customer_home_payload(customer: CustomerProfile) -> dict:
    now = timezone.now()
    cutoff_new = now - timedelta(hours=24)

    open_orders = Order.objects.filter(
        owner=customer,
        status__in=(OrderStatus.OPEN, OrderStatus.PARTIALLY_SETTLED),
    ).count()

    pending_items = OrderItem.objects.filter(
        order__owner=customer,
        status__in=(
            OrderItemStatus.PENDING_SELLER_APPROVAL,
            OrderItemStatus.SELLER_APPROVED,
        ),
    ).count()

    notifications: list[dict] = []
    recent_items = (
        OrderItem.objects.filter(order__owner=customer)
        .select_related("order", "asset", "seller")
        .order_by("-created")[:10]
    )
    for oi in recent_items:
        notifications.append({
            "kind": "order_item",
            "title": f"طلب #{oi.order_id} — {oi.asset.title}",
            "created_at": oi.created.isoformat(),
            "time_hint": _ar_time_hint(oi.created),
            "is_new": oi.created >= cutoff_new,
        })

    new_count = sum(1 for n in notifications if n["is_new"])

    return {
        "nickname": customer.user.username or "",
        "balance": str(customer.balance),
        "currency": str(customer.currency),
        "open_orders": open_orders,
        "pending_order_items": pending_items,
        "notifications": notifications,
        "notifications_new_count": new_count,
    }
```

---

## 15. `apps/users/views.py` — Modify

**Purpose:** Add `home_summary` action to both mobile viewsets and wire up the
new imports.

**Changes required:**

**Add imports at the top of the file:**
```python
from apps.users.customer_home import build_customer_home_payload
from apps.users.seller_home import build_seller_home_payload
```

**Add to `SellerProfileMobileViewSet` (after existing methods):**
```python
@action(detail=False, methods=["get"], url_path="home-summary")
def home_summary(self, request):
    """Aggregated metrics, notifications, and hints for the seller home UI."""
    profile = getattr(request.user, "active_profile", None)
    if not profile or profile.type != ProfileType.SELLER:
        return Response(
            {"detail": "Active seller profile required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    seller = SellerProfile.objects.filter(pk=profile.pk).first()
    if seller is None:
        return Response(
            {"detail": "Active seller profile required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(build_seller_home_payload(seller))
```

**Add to `CustomerProfileMobileViewSet` (after existing methods):**
```python
@action(detail=False, methods=["get"], url_path="home-summary")
def home_summary(self, request):
    customer = (
        CustomerProfile.objects.filter(
            user=request.user, deleted__isnull=True, is_valid=True
        )
        .first()
    )
    if customer is None:
        return Response(
            {"detail": "Active customer profile required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(build_customer_home_payload(customer))
```

**Resulting new endpoints (registered automatically by existing DRF router in
`apps/users/urls.py` — no routing change needed):**
- `GET /api/users/mobile/sellers/home-summary/`
- `GET /api/users/mobile/customers/home-summary/`

---

## 16. `ppw/seed_view.py` — Create

**Purpose:** Token-gated HTTP `POST /api/internal/seed/` endpoint that
idempotently creates demo data (11 user accounts, birds, auctions, bids,
orders, product assets). Intended for staging and review environments.
Protected by `X-Migrate-Token` header matching the `MIGRATE_TOKEN` env var.

The full seed script is long. Copy it verbatim from
`PPW-master/ppw/seed_view.py`. Key accounts it creates:

| Username | Role | Phone |
|----------|------|-------|
| `manager1` | Manager | +201100000001 |
| `seller1` | Seller (EG) | +201100000002 |
| `seller2` | Seller (EG) | +201100000003 |
| `seller_kw` | Seller (KW) | +96541234567 |
| `seller_sa` | Seller (SA) | +966501234567 |
| `customer1` | Customer (EG) | +201100000006 |
| `customer2` | Customer (EG) | +201100000007 |
| `customer_ae` | Customer (AE) | +971501234567 |
| `customer_jo` | Customer (JO) | +962791234567 |
| `customer_ma` | Customer (MA) | +212601234567 |
| `demo_customer` | Customer | +201000000006 |

All passwords follow the pattern in `ACCOUNTS` dict in the file.
The seed is re-runnable: it hard-deletes previous seed accounts first.

---

## 17. `ppw/urls.py` — Modify

**Purpose:** Register the new `pigeon_core` API namespace and the internal
`migrate` + `seed` endpoints.

**Add these imports at the top of the file:**
```python
import os
from django.core.management import call_command
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from ppw import seed_view
```

**Add the migrate view function before `urlpatterns`:**
```python
@csrf_exempt
@require_POST
def run_migrate(request):
    token = request.headers.get("X-Migrate-Token", "")
    if not token or token != os.environ.get("MIGRATE_TOKEN", ""):
        return JsonResponse({"error": "forbidden"}, status=403)
    call_command("migrate", "--no-input", verbosity=1)
    return JsonResponse({"status": "ok"})
```

**Add to `urlpatterns`:**
```python
path("api/core/", include("apps.pigeon_core.urls")),
path("api/internal/migrate/", run_migrate),
path("api/internal/seed/", seed_view.run_seed),
```

**Final `urlpatterns` should look like:**
```python
urlpatterns = [
    path("api/auth/", include("apps.authentication.urls")),
    path("api/users/", include("apps.users.urls")),
    path("api/assets/", include("apps.assets.urls")),
    path("api/auctions/", include("apps.auctions.urls")),
    path("api/orders/", include("apps.orders.urls")),
    path("api/", include("apps.ratings.urls")),
    path("api/", include("apps.races.urls")),
    path("api/", include("apps.packages.urls")),
    path("api/loyalty/", include("apps.loyalty.urls")),
    path("api/core/", include("apps.pigeon_core.urls")),
    path("api/internal/migrate/", run_migrate),
    path("api/internal/seed/", seed_view.run_seed),
    path("api/schema/", InternalSchemaAPIView.as_view(), name="api-schema"),
]
```

---

## 18. `ppw/settings/base.py` — Modify

**Purpose:** Register `apps.pigeon_core` in Django's app registry so its
models, signals, and admin are loaded.

**In the `APPS` list, add `"apps.pigeon_core"`:**
```python
APPS = [
    "apps.assets",
    "apps.orders",
    "apps.users",
    "apps.general",
    "apps.authentication",
    "apps.auctions",
    "apps.ratings",
    "apps.races",
    "apps.packages",
    "apps.loyalty",
    "apps.pigeon_core",   # ← add this
]
```

---

## 19. `tests/authentication/test_profile_switching.py` — Modify

**Purpose:** Add a test that verifies a seller-only account (no
`CustomerProfile` row) can successfully switch to `Customer` via the new
auto-create logic.

**Add this test to the existing `TestProfileSwitching` class:**
```python
def test_switch_to_customer_creates_profile_when_only_seller_exists(self):
    """Seller-only accounts (no Customer row) can switch to Customer."""
    user = UserAccountFactory(with_profile=False)
    SellerProfileFactory(user=user, is_active=True)

    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    data = {"new_profile": ProfileType.CUSTOMER}
    response = client.post("/api/auth/switch-profile/", data)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["profile"] == ProfileType.CUSTOMER

    user.refresh_from_db()
    assert user.active_profile.type == ProfileType.CUSTOMER
    assert CustomerProfile.objects.filter(user=user).count() == 1
```

**Required imports** (add to the top of the test file if not already present):
```python
from rest_framework.test import APIClient
from apps.authentication.services import build_token_for_user
from apps.users.models import CustomerProfile
from tests.factories import SellerProfileFactory, UserAccountFactory
```

---

## Implementation order

Follow this sequence to avoid import errors and migration conflicts:

1. `ppw/settings/base.py` — add `apps.pigeon_core` to `INSTALLED_APPS`
2. `apps/pigeon_core/` — create the entire app (all files)
3. Run `python manage.py migrate` — creates the 6 new tables and seeds packages
4. `apps/authentication/services.py` — apply the modified version
5. `apps/users/seller_home.py` — create
6. `apps/users/customer_home.py` — create
7. `apps/users/views.py` — add imports and `home_summary` actions
8. `ppw/seed_view.py` — create
9. `ppw/urls.py` — add routes
10. `tests/authentication/test_profile_switching.py` — add test
11. Run `pytest tests/authentication -q` to verify the new test passes

---

## Status value alignment checklist

Before deploying, verify these enum values match `apps/orders/models.py`
in the current PPW codebase:

- [ ] `OrderItemStatus.PENDING_SELLER_APPROVAL` — used in `seller_home.py` and `customer_home.py`
- [ ] `OrderItemStatus.SELLER_APPROVED` — used in `seller_home.py` and `customer_home.py`
- [ ] `OrderItemStatus.SETTLED` — used in `seller_home.py`
- [ ] `OrderStatus.OPEN` — used in `customer_home.py`
- [ ] `OrderStatus.PARTIALLY_SETTLED` — used in `customer_home.py`
- [ ] `AuctionStatus.ACTIVE` — used in `seller_home.py`
- [ ] `AuctionItemStatus.SOLD` — used in `seller_home.py`

If any name differs in the current PPW, update the import and reference in the
respective file.

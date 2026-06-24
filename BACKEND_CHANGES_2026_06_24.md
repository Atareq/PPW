# Backend Changes — 2026-06-24

All modifications made to the Django REST Framework backend during the module gap-fix session.  
**No model or migration changes** — viewsets, serializers, and URL patterns only.

---

## 1. Lucky Wheel — Admin Spin Identity

### Why
The existing `LuckyWheelSpinSerializer` was built for customer-facing use and exposed no user identity fields. The admin dashboard spin history table needs to show *who* made each spin.

### `apps/lucky_wheel/serializers.py`

```diff
+class AdminLuckyWheelSpinSerializer(serializers.ModelSerializer):
+    username = serializers.CharField(source="user.username", read_only=True)
+
+    class Meta:
+        model = LuckyWheelSpin
+        fields = [
+            "id",
+            "user_id",
+            "profile_id",
+            "username",
+            "grant",
+            "prize",
+            "status",
+            "prize_snapshot",
+            "awarded_at",
+            "metadata",
+            "created",
+        ]
+        read_only_fields = fields
```

### `apps/lucky_wheel/views.py`

```diff
 from apps.lucky_wheel.serializers import (
+    AdminLuckyWheelSpinSerializer,
     LuckyWheelCurrentSerializer,
     ...
 )

-    serializer_class = LuckyWheelSpinSerializer
+    serializer_class = AdminLuckyWheelSpinSerializer
```

---

## 2. Orders — Item Detail Endpoint + Status Filter

### Why
- Mobile needed `GET /orders/items/<pk>/` to fetch a single order item by ID (e.g. after tapping a push notification).
- Sellers and managers needed to filter the order-item queue by `status`. Without it, `?status=pending` query params were silently ignored.

### `apps/orders/views.py`

```diff
-class OrderItemViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
+class OrderItemViewSet(
+    mixins.ListModelMixin,
+    mixins.RetrieveModelMixin,
+    viewsets.GenericViewSet,
+):
     ...
-    filterset_fields = ["discount_offer", "cashback_offer"]
+    filterset_fields = ["status", "discount_offer", "cashback_offer"]
```

### `apps/orders/urls.py`

```diff
+order_item_detail = OrderItemViewSet.as_view({"get": "retrieve"})

 urlpatterns = [
     ...
     path("items/", order_item_list, name="order-item-list"),
+    path("items/<int:pk>/", order_item_detail, name="order-item-detail"),
     ...
 ]
```

---

## 3. Packages — Status Filter for Manager View

### Why
The admin dashboard packages page filters seller packages by status (`active`, `expired`, `cancelled`). `ManagerSellerPackageViewSet` was missing `filter_backends` entirely, so `?status=active` was silently ignored and the full unfiltered list was always returned.

### `apps/packages/views.py`

```diff
 class ManagerSellerPackageViewSet(...):
     queryset = SellerPackage.objects.all()
     serializer_class = SellerPackageSerializer
+    filter_backends = [DjangoFilterBackend]
+    filterset_fields = ["status"]
```

---

## 4. Promotions — Customer Grants Endpoint

### Why
`UserPromotionGrant` records are created when a user wins a Lucky Wheel prize, completes onboarding, or uses a referral code. `UserPromotionGrantService.get_active_grants_for_user()` already existed but was never exposed via an API endpoint — customers had no way to view their personalized offers.

### `apps/promotions/views.py`

```diff
 from apps.promotions.models import (
+    UserPromotionGrant,
     ...
 )
 from apps.promotions.serializers import (
+    UserPromotionGrantSerializer,
     ...
 )
-from apps.promotions.services import CashbackOfferService, DiscountOfferService
+from apps.promotions.services import CashbackOfferService, DiscountOfferService, UserPromotionGrantService

+class MyPromotionGrantViewSet(
+    mixins.ListModelMixin,
+    viewsets.GenericViewSet,
+):
+    """Lists the calling user's active, non-expired promotion grants."""
+
+    permission_classes = [HasAppAccess]
+    permission_resource = "CASHBACK"
+    serializer_class = UserPromotionGrantSerializer
+    queryset = UserPromotionGrant.objects.all()
+
+    def get_queryset(self):
+        return UserPromotionGrantService.get_active_grants_for_user(
+            user=self.request.user,
+            profile=self.request.user.active_profile,
+        )
```

> **Note:** Uses `CASHBACK` permission resource — same scope as `cashback/my/` and `cashback/balance/` since grants are part of the same customer promotions scope. Uses the existing `UserPromotionGrantSerializer` as-is.

### `apps/promotions/urls.py`

```diff
 from apps.promotions.views import (
+    MyPromotionGrantViewSet,
     ...
 )

 urlpatterns = [
     ...
     path("cashback/my/", MyCashbackTransactionViewSet.as_view(LIST_ACTION), ...),
+    path(
+        "my-grants/",
+        MyPromotionGrantViewSet.as_view(LIST_ACTION),
+        name="my-promotion-grant-list",
+    ),
     path("cashback/balance/", ...),
     ...
 ]
```

**New endpoint:** `GET /api/promotions/my-grants/`  
Returns paginated list of the authenticated user's active, non-expired `UserPromotionGrant` records.

---

## 5. Seasons — Finalize Action

### Why
The `Season` model had `FINALIZED` status and a `finalized_by` FK to `ManagerProfile` but no API action to trigger the transition. After running "Calculate Winners", managers need a "Finalize" action to lock the season, record who approved it, and signal prizes have been distributed.

**Business rule:** Only seasons in `calculated` status can be finalized. Attempting to finalize a `draft` or already-`finalized` season returns a 400.

### `apps/seasons/views.py`

```diff
-from apps.seasons.models import Season
+from apps.seasons.models import Season, SeasonStatus

+class SeasonFinalizeViewSet(viewsets.GenericViewSet):
+    permission_classes = [HasAppAccess]
+    permission_resource = "SEASONS_ADMIN"
+    manager_permission_map = {"create": "seasons.finalize"}
+    serializer_class = SeasonSerializer
+    queryset = Season.objects.all()
+
+    @extend_schema(tags=["Seasons"], request=None, responses={200: SeasonSerializer})
+    def create(self, request, *args, **kwargs):
+        season = self.get_object()
+        if season.status != SeasonStatus.CALCULATED:
+            raise ValidationError(
+                {"detail": "Only calculated seasons can be finalized."}
+            )
+        season.status = SeasonStatus.FINALIZED
+        season.finalized_by = request.user.active_profile
+        season.save(update_fields=["status", "finalized_by", "modified"])
+        return Response(SeasonSerializer(season).data, status=status.HTTP_200_OK)
```

### `apps/seasons/urls.py`

```diff
 from apps.seasons.views import (
+    SeasonFinalizeViewSet,
     SeasonCalculateWinnersViewSet,
     ...
 )

 urlpatterns = [
     ...
     path("<int:pk>/calculate-winners/", ...),
+    path(
+        "<int:pk>/finalize/",
+        SeasonFinalizeViewSet.as_view({"post": "create"}),
+        name="season-finalize",
+    ),
     path("<int:pk>/seller-insights/", ...),
 ]
```

**New endpoint:** `POST /api/seasons/<pk>/finalize/`  
Transitions the season from `calculated` → `finalized`. Records `finalized_by` as the calling manager's active profile. Returns the updated `Season` object.

---

## Summary

| File | Change | Reason |
|---|---|---|
| `apps/lucky_wheel/serializers.py` | Added `AdminLuckyWheelSpinSerializer` | Admin spin list needs `user_id`, `profile_id`, `username` |
| `apps/lucky_wheel/views.py` | Switched to `AdminLuckyWheelSpinSerializer` | Wire new serializer into admin endpoint |
| `apps/orders/views.py` | Added `RetrieveModelMixin` + `status` to filterset | Single item fetch + status-based queue filtering |
| `apps/orders/urls.py` | Added `GET /orders/items/<pk>/` | Expose item detail retrieve |
| `apps/packages/views.py` | Added `DjangoFilterBackend` + `status` filter | `?status=` param was silently ignored |
| `apps/promotions/views.py` | Added `MyPromotionGrantViewSet` | Expose customer's active promotion grants |
| `apps/promotions/urls.py` | Added `GET /promotions/my-grants/` | Wire grants endpoint |
| `apps/seasons/views.py` | Added `SeasonFinalizeViewSet` | Season finalize state-transition |
| `apps/seasons/urls.py` | Added `POST /seasons/<pk>/finalize/` | Wire finalize endpoint |

**No migrations needed.** All changes are viewset/serializer/URL additions only.

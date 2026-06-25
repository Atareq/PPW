from rest_framework.routers import DefaultRouter

from apps.transfers.views import OwnershipTransferViewSet

router = DefaultRouter()
router.register("", OwnershipTransferViewSet, basename="ownership-transfer")

urlpatterns = router.urls

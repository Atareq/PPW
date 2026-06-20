from django.db import transaction
from django.http import Http404
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.authentication.services import switch_user_profile
from apps.general.authorizations import HasAppAccess, HasStaffRole
from apps.general.services import AuditLogService
from apps.users.constants import ProfileType, StaffRole
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile
from apps.users.serializers import (
    CustomerProfileAdminSerializer,
    CustomerProfileMobileSerializer,
    SellerProfileAdminSerializer,
    SellerProfileMobileSerializer,
    StaffMemberCreateSerializer,
    StaffMemberListSerializer,
    StaffMemberUpdateSerializer,
)


class SellerProfileAdminViewSet(viewsets.ModelViewSet):
    """Admin/staff CRUD for seller profile moderation and correction."""

    serializer_class = SellerProfileAdminSerializer
    queryset = SellerProfile.objects.select_related('user').all().order_by("-created")
    permission_classes = [HasAppAccess]
    permission_resource = "SELLER_PROFILE_ADMIN"

    @action(detail=False, methods=["post"], url_path="bulk-suspend")
    def bulk_suspend(self, request):
        ids = request.data.get("ids", [])
        updated = SellerProfile.objects.filter(pk__in=ids).update(is_valid=False)
        AuditLogService.log(
            request, action="bulk_suspend", resource_type="SellerProfile",
            details={"ids": ids, "updated": updated},
        )
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-restore")
    def bulk_restore(self, request):
        ids = request.data.get("ids", [])
        updated = SellerProfile.objects.filter(pk__in=ids).update(is_valid=True)
        AuditLogService.log(
            request, action="bulk_restore", resource_type="SellerProfile",
            details={"ids": ids, "updated": updated},
        )
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-activate")
    def bulk_activate(self, request):
        ids = request.data.get("ids", [])
        updated = SellerProfile.objects.filter(pk__in=ids).update(activated=True)
        AuditLogService.log(
            request, action="bulk_activate", resource_type="SellerProfile",
            details={"ids": ids, "updated": updated},
        )
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="stats")
    def stats(self, request, pk=None):
        from apps.assets.models import Bird, Supply, Accessory, Feed, Supplement
        from apps.auctions.models import Auction
        from apps.packages.models import SellerPackage
        from apps.loyalty.models import BadgeAward

        seller = self.get_object()

        birds_count = Bird.objects.filter(owner=seller, deleted__isnull=True).count()
        products_count = (
            Supply.objects.filter(owner=seller, deleted__isnull=True).count()
            + Accessory.objects.filter(owner=seller, deleted__isnull=True).count()
            + Feed.objects.filter(owner=seller, deleted__isnull=True).count()
            + Supplement.objects.filter(owner=seller, deleted__isnull=True).count()
        )
        auctions_count = Auction.objects.filter(owner=seller).count()

        active_pkg = (
            SellerPackage.objects.filter(owner=seller, status="active")
            .select_related("package")
            .first()
        )
        active_package = None
        if active_pkg:
            active_package = {
                "id": active_pkg.id,
                "name": active_pkg.package.name,
                "remaining_points": active_pkg.remaining_points,
                "total_points": active_pkg.package.points,
                "expires_at": active_pkg.expires_at.isoformat() if active_pkg.expires_at else None,
            }

        badge_awards = BadgeAward.objects.filter(profile=seller).values("badge_type", "awarded_at")
        badges = [
            {
                "badge_type": b["badge_type"],
                "awarded_at": b["awarded_at"].isoformat() if b["awarded_at"] else None,
            }
            for b in badge_awards
        ]

        return Response({
            "birds_count": birds_count,
            "products_count": products_count,
            "auctions_count": auctions_count,
            "active_package": active_package,
            "badges": badges,
        })

    def perform_destroy(self, instance):
        AuditLogService.log(
            self.request, action="destroy", resource_type="SellerProfile",
            resource_id=instance.pk,
        )
        super().perform_destroy(instance)


class CustomerProfileAdminViewSet(viewsets.ModelViewSet):
    """Admin/staff CRUD for customer profile moderation and correction."""

    serializer_class = CustomerProfileAdminSerializer
    queryset = CustomerProfile.objects.select_related('user').all().order_by("-created")
    permission_classes = [HasAppAccess]
    permission_resource = "CUSTOMER_PROFILE_ADMIN"

    @action(detail=False, methods=["post"], url_path="bulk-suspend")
    def bulk_suspend(self, request):
        ids = request.data.get("ids", [])
        updated = CustomerProfile.objects.filter(pk__in=ids).update(is_valid=False)
        AuditLogService.log(
            request, action="bulk_suspend", resource_type="CustomerProfile",
            details={"ids": ids, "updated": updated},
        )
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-restore")
    def bulk_restore(self, request):
        ids = request.data.get("ids", [])
        updated = CustomerProfile.objects.filter(pk__in=ids).update(is_valid=True)
        AuditLogService.log(
            request, action="bulk_restore", resource_type="CustomerProfile",
            details={"ids": ids, "updated": updated},
        )
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-activate")
    def bulk_activate(self, request):
        ids = request.data.get("ids", [])
        updated = CustomerProfile.objects.filter(pk__in=ids).update(activated=True)
        AuditLogService.log(
            request, action="bulk_activate", resource_type="CustomerProfile",
            details={"ids": ids, "updated": updated},
        )
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    def perform_destroy(self, instance):
        AuditLogService.log(
            self.request, action="destroy", resource_type="CustomerProfile",
            resource_id=instance.pk,
        )
        super().perform_destroy(instance)


class StaffManagementViewSet(viewsets.ModelViewSet):
    """
    Super Admin manages staff members (ManagerProfile accounts).
    Reads: Super Admin + Admin.  Writes: Super Admin only.
    Destroy deactivates the account — never hard-deletes.
    """

    queryset = ManagerProfile.objects.select_related("user").all().order_by("-created")
    permission_classes = [HasStaffRole]
    required_staff_roles = [StaffRole.SUPER_ADMIN]
    read_staff_roles = [StaffRole.SUPER_ADMIN, StaffRole.ADMIN]

    def get_serializer_class(self):
        if self.action == "create":
            return StaffMemberCreateSerializer
        if self.action in ("update", "partial_update"):
            return StaffMemberUpdateSerializer
        return StaffMemberListSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        AuditLogService.log(
            request, action="create_staff", resource_type="ManagerProfile",
            resource_id=profile.pk,
            details={"staff_role": profile.staff_role},
        )
        return Response(
            StaffMemberListSerializer(profile).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        AuditLogService.log(
            self.request, action="update_staff", resource_type="ManagerProfile",
            resource_id=instance.pk,
            details={"staff_role": instance.staff_role},
        )

    def destroy(self, request, *args, **kwargs):
        """Deactivate instead of hard-delete."""
        instance = self.get_object()
        instance.is_active = False
        instance.is_valid = False
        instance.save(update_fields=["is_active", "is_valid", "modified"])
        AuditLogService.log(
            request, action="deactivate_staff", resource_type="ManagerProfile",
            resource_id=instance.pk,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SellerProfileMobileViewSet(viewsets.ModelViewSet):
    """User-facing seller profile flow; creation starts inactive for review."""

    serializer_class = SellerProfileMobileSerializer
    queryset = SellerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(
            user=request.user,
            is_active=False,
            activated=True,
            type=ProfileType.SELLER,
        )
        return Response(
            {"message": "Room created successfully."},
            status=status.HTTP_201_CREATED,
        )

    def get_queryset(self):
        return SellerProfile.objects.select_related('user').filter(
            user=self.request.user, deleted__isnull=True
        ).order_by("created")

    def get_object(self):
        pk = self.kwargs.get("pk")
        if pk:
            qs = self.get_queryset().filter(pk=pk)
            if not qs.exists():
                raise Http404
            return qs.first()
        obj = self.get_queryset().filter(is_active=True).first()
        if obj is None:
            raise Http404
        return obj

    def perform_destroy(self, instance):
        if instance.is_active:
            other_profile = instance.user.profiles.exclude(
                id=instance.id
            ).first()
            if other_profile:
                switch_user_profile(instance.user, other_profile.type)
            instance.is_active = False
            instance.is_valid = False
        instance.delete()


class CustomerProfileMobileViewSet(viewsets.ModelViewSet):
    """User-facing customer profile flow scoped to the authenticated account."""

    serializer_class = CustomerProfileMobileSerializer
    queryset = CustomerProfile.objects.all().order_by("-created")
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return CustomerProfile.objects.select_related('user').filter(
            user=self.request.user, deleted__isnull=True
        ).order_by("-created")

    def perform_destroy(self, instance):
        if instance.is_active:
            other_profile = instance.user.profiles.exclude(
                id=instance.id
            ).first()
            if other_profile:
                switch_user_profile(instance.user, other_profile.type)
            instance.is_active = False
            instance.is_valid = False
        instance.delete()

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.packages.models import Package, SellerPackage, SellerPackageStatus
from apps.packages.serializers import (
    PackageSerializer,
    SellerPackageActiveSerializer,
    SellerPackageRequestSerializer,
    SellerPackageSerializer,
    SellerPackageStatusTransitionSerializer,
)
from apps.packages.services import (
    SellerPackageService,
    seller_package_queryset,
)
from apps.users.constants import ProfileType


def truthy_query_param(value):
    return str(value).lower() in {"1", "true", "yes", "on"}


@extend_schema_view(
    list=extend_schema(tags=["Packages"], responses={200: PackageSerializer}),
    create=extend_schema(
        tags=["Packages"],
        request=PackageSerializer,
        responses={201: PackageSerializer},
    ),
    partial_update=extend_schema(
        tags=["Packages"],
        request=PackageSerializer,
        responses={200: PackageSerializer},
    ),
)
class PackageViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Manager package plan management and seller-visible package catalog."""

    permission_classes = [HasAppAccess]
    permission_resource = "PACKAGE"
    manager_permission_namespace = "packages"
    queryset = Package.objects.all()
    serializer_class = PackageSerializer

    def get_queryset(self):
        queryset = Package.objects.all().order_by("-created")
        role = self.request.user.role

        if role == ProfileType.MANAGER:
            return queryset

        if role == ProfileType.SELLER:
            return queryset.filter(is_available=True)

        return queryset.none()

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


@extend_schema_view(
    list=extend_schema(
        tags=["Seller Packages"],
        responses={200: SellerPackageSerializer},
    ),
    create=extend_schema(
        tags=["Seller Packages"],
        request=SellerPackageRequestSerializer,
        responses={201: SellerPackageSerializer},
    ),
)
class MySellerPackageViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Seller package requests and active-package lookup for the current seller."""

    permission_classes = [HasAppAccess]
    permission_resource = "MY_SELLER_PACKAGE"
    queryset = SellerPackage.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status", "activated"]

    def get_serializer_class(self):
        if self.action == "create":
            return SellerPackageRequestSerializer
        return SellerPackageSerializer

    def get_queryset(self):
        queryset = seller_package_queryset()
        active_profile = self.request.user.active_profile
        if active_profile.type != ProfileType.SELLER:
            return queryset.none()
        return queryset.filter(owner_id=active_profile.pk)

    def list(self, request, *args, **kwargs):
        if truthy_query_param(request.query_params.get("active", "false")):
            try:
                active_package = SellerPackageService.get_active_package(
                    request.user.active_profile
                )
            except NotFound:
                return Response(None, status=status.HTTP_200_OK)
            serializer = SellerPackageActiveSerializer(
                active_package,
                context=self.get_serializer_context(),
            )
            return Response(serializer.data, status=status.HTTP_200_OK)
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        seller_package = serializer.save()
        response_serializer = SellerPackageSerializer(
            seller_package,
            context=self.get_serializer_context(),
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Seller Packages"],
        responses={200: SellerPackageSerializer},
    ),
    partial_update=extend_schema(
        tags=["Seller Packages"],
        request=SellerPackageStatusTransitionSerializer,
        responses={200: SellerPackageSerializer},
    ),
)
class ManagerSellerPackageViewSet(
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Manager review flow for seller package activation or cancellation."""

    permission_classes = [HasAppAccess]
    permission_resource = "SELLER_PACKAGE"
    manager_permission_map = {
        "list": "seller_packages.list",
        "retrieve": "seller_packages.retrieve",
    }
    queryset = SellerPackage.objects.all()
    serializer_class = SellerPackageSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status"]

    def get_required_manager_permission(self):
        if self.action in {"list", "retrieve"}:
            return self.manager_permission_map.get(self.action)
        if self.action != "partial_update":
            return None
        next_status = self.request.data.get("status")
        if next_status == SellerPackageStatus.ACTIVE:
            return "seller_packages.activate"
        if next_status == SellerPackageStatus.CANCELLED:
            return "seller_packages.cancel"
        return None

    def get_queryset(self):
        return seller_package_queryset()

    def partial_update(self, request, *args, **kwargs):
        active_profile = request.user.active_profile
        if active_profile.type != ProfileType.MANAGER:
            raise PermissionDenied("Only manager profiles can update seller packages.")

        seller_package = self.get_object()
        serializer = SellerPackageStatusTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        next_status = serializer.validated_data["status"]

        if next_status == SellerPackageStatus.ACTIVE:
            seller_package = SellerPackageService.activate_package(seller_package)
        elif next_status == SellerPackageStatus.CANCELLED:
            seller_package = SellerPackageService.cancel_package(seller_package)

        response_serializer = SellerPackageSerializer(
            seller_package,
            context=self.get_serializer_context(),
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

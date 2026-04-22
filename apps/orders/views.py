from django.db.models import Prefetch
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.orders.models import Order, OrderItem
from apps.orders.serializers import (
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderItemSerializer,
    OrderListSerializer,
)
from apps.orders.services import OrderItemService
from apps.users.constants import ProfileType


def order_item_queryset():
    return OrderItem.objects.select_related(
        "order",
        "asset",
        "seller",
        "seller__user",
    ).order_by("-created")


class OrderViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [HasAppAccess]
    permission_resource = "ORDER"

    queryset = Order.objects.all()

    def get_serializer_class(self):
        if self.action == "create":
            return OrderCreateSerializer
        if self.action == "retrieve":
            return OrderDetailSerializer
        return OrderListSerializer

    def get_queryset(self):
        queryset = (
            Order.objects.select_related("owner", "owner__user")
            .prefetch_related(
                Prefetch("items", queryset=order_item_queryset())
            )
            .order_by("-created")
        )
        active_profile = self.request.user.active_profile

        if not active_profile:
            return queryset.none()

        if active_profile.type == ProfileType.MANAGER:
            return queryset

        if active_profile.type == ProfileType.CUSTOMER:
            return queryset.filter(owner_id=active_profile.pk)

        return queryset.none()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()

        response_serializer = OrderDetailSerializer(
            order,
            context=self.get_serializer_context(),
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )


class OrderItemViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "ORDER_ITEM"
    permission_owner_field = "seller_id"
    serializer_class = OrderItemSerializer
    queryset = OrderItem.objects.all()

    def get_queryset(self):
        queryset = order_item_queryset()
        user = self.request.user
        active_profile = getattr(user, "active_profile", None)

        if user.role == ProfileType.MANAGER:
            return queryset

        if active_profile and active_profile.type == ProfileType.SELLER:
            return queryset.filter(seller_id=active_profile.pk)

        return queryset.none()

    def approve(self, request, pk=None):
        item = self.get_object()
        seller = self._get_active_seller_profile()
        item = OrderItemService.approve_item(item, seller)
        return Response(
            self.get_serializer(item).data,
            status=status.HTTP_200_OK,
        )

    def reject(self, request, pk=None):
        item = self.get_object()
        seller = self._get_active_seller_profile()
        item = OrderItemService.reject_item(item, seller)
        return Response(
            self.get_serializer(item).data,
            status=status.HTTP_200_OK,
        )

    def _get_active_seller_profile(self):
        active_profile = self.request.user.active_profile
        if not active_profile or active_profile.type != ProfileType.SELLER:
            raise PermissionDenied(
                "Only seller profiles can manage order items."
            )
        return active_profile

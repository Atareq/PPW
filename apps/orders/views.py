from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.general.authorizations import HasAppAccess
from apps.orders.models import Order, OrderItem
from apps.orders.serializers import (
    CartItemCreateSerializer,
    CartItemUpdateSerializer,
    CartSerializer,
    CheckoutSerializer,
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderItemSerializer,
    OrderListSerializer,
    OrderSummarySerializer,
)
from apps.orders.services import (
    CartService,
    OrderCheckoutService,
    OrderItemService,
    order_item_queryset,
)
from apps.users.constants import ProfileType


class ActiveCustomerPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        active_profile = getattr(user, "active_profile", None)
        return (
            active_profile is not None
            and active_profile.type == ProfileType.CUSTOMER
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Orders"],
        request=OrderCreateSerializer,
        responses={201: OrderSummarySerializer},
    )
)
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

        response_serializer = OrderSummarySerializer(
            order,
            context=self.get_serializer_context(),
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(tags=["Orders"], responses={200: CartSerializer}),
)
class CartAPIView(APIView):
    permission_classes = [ActiveCustomerPermission]

    def get(self, request):
        cart = CartService.get_or_create_active_cart(
            request.user.active_profile
        )
        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)


@extend_schema_view(
    post=extend_schema(
        tags=["Orders"],
        request=CartItemCreateSerializer,
        responses={201: CartSerializer},
    ),
)
class CartItemCreateAPIView(APIView):
    permission_classes = [ActiveCustomerPermission]

    def post(self, request):
        serializer = CartItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = CartService.add_item(
            request.user.active_profile,
            serializer.validated_data["asset_id"],
            serializer.validated_data["quantity"],
        )
        return Response(
            CartSerializer(cart).data, status=status.HTTP_201_CREATED
        )


@extend_schema_view(
    patch=extend_schema(
        tags=["Orders"],
        request=CartItemUpdateSerializer,
        responses={200: CartSerializer},
    ),
    delete=extend_schema(
        tags=["Orders"],
        request=None,
        responses={204: None},
    ),
)
class CartItemDetailAPIView(APIView):
    permission_classes = [ActiveCustomerPermission]

    def patch(self, request, pk):
        serializer = CartItemUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = CartService.update_item(
            request.user.active_profile,
            pk,
            serializer.validated_data["quantity"],
        )
        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        CartService.remove_item(request.user.active_profile, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    delete=extend_schema(
        tags=["Orders"],
        request=None,
        responses={204: None},
    ),
)
class CartClearAPIView(APIView):
    permission_classes = [ActiveCustomerPermission]

    def delete(self, request):
        CartService.clear_cart(request.user.active_profile)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    post=extend_schema(
        tags=["Orders"],
        request=CheckoutSerializer,
        responses={201: OrderSummarySerializer},
    ),
)
class CheckoutAPIView(APIView):
    permission_classes = [ActiveCustomerPermission]

    def post(self, request):
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = OrderCheckoutService.checkout_cart(request.user.active_profile)
        return Response(
            OrderSummarySerializer(order).data,
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

    @extend_schema(
        tags=["Orders"],
        request=None,
        responses={200: OrderItemSerializer},
    )
    def approve(self, request, pk=None):
        item = self.get_object()
        seller = self._get_active_seller_profile()
        item = OrderItemService.approve_item(item, seller)
        return Response(
            self.get_serializer(item).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Orders"],
        request=None,
        responses={200: OrderItemSerializer},
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

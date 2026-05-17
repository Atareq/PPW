from django.db.models import Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.orders.models import Cart, Order, OrderItem
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
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status"]

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


class ActiveCartViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "CART"
    serializer_class = CartSerializer
    queryset = Cart.objects.all()

    @extend_schema(
        tags=["Cart"],
        summary="Retrieve active cart",
        description="Returns the customer's active cart, creating one when none exists.",  # noqa
        responses={200: CartSerializer},
    )
    def retrieve(self, request):
        cart = CartService.get_or_create_active_cart(
            request.user.active_profile
        )
        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Cart"],
        summary="Add item to cart",
        description="Adds a market-listed asset to the active cart, "
        "merging quantity when the asset is already present.",
        request=CartItemCreateSerializer,
        responses={201: CartSerializer},
    )
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

    @extend_schema(
        tags=["Cart"],
        summary="Update cart item",
        description="Updates the quantity of one item in the customer's active cart.",
        request=CartItemUpdateSerializer,
        responses={200: CartSerializer},
    )
    def patch(self, request, pk=None):
        serializer = CartItemUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = CartService.update_item(
            request.user.active_profile,
            pk,
            serializer.validated_data["quantity"],
        )
        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Cart"],
        summary="Remove cart item",
        description="Removes one item from the customer's active cart.",
        request=None,
        responses={204: None},
    )
    def remove_cart_item(self, request, pk=None):
        CartService.remove_item(request.user.active_profile, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=["Cart"],
        summary="Clear active cart",
        description="Removes all items from the customer's active cart.",
        request=None,
        responses={204: None},
    )
    def delete(self, request):
        CartService.clear_cart(request.user.active_profile)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CheckoutViewSet(viewsets.GenericViewSet):
    permission_classes = [HasAppAccess]
    permission_resource = "CHECKOUT"
    serializer_class = CheckoutSerializer
    queryset = Order.objects.all()

    @extend_schema(
        tags=["Checkout"],
        summary="Checkout active cart",
        description="Converts the customer's active cart into pending "
        "seller decision order items after stock validation.",
        request=CheckoutSerializer,
        responses={201: OrderSummarySerializer},
    )
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
        active_profile = user.active_profile

        if user.role == ProfileType.MANAGER:
            return queryset

        if active_profile.type == ProfileType.SELLER:
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
        if active_profile.type != ProfileType.SELLER:
            raise PermissionDenied(
                "Only seller profiles can manage order items."
            )
        return active_profile

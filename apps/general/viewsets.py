from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess


class CreateListViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    Base viewset for endpoints that only expose create and list actions.

    Subclasses must provide separate serializers for write input and read/list
    output. Create responses intentionally use the list serializer.
    """

    permission_classes = [HasAppAccess]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    ordering_fields = [
        "created",
        "modified",
    ]
    ordering = ["-created"]

    create_serializer_class = None
    list_serializer_class = None

    def get_serializer_class(self):
        if self.action == "create":
            return self._get_required_serializer_class("create_serializer_class")
        return self._get_required_serializer_class("list_serializer_class")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        response_serializer_class = self._get_required_serializer_class(
            "list_serializer_class"
        )
        response_serializer = response_serializer_class(
            instance,
            context=self.get_serializer_context(),
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    def _get_required_serializer_class(self, attribute_name):
        serializer_class = getattr(self, attribute_name, None)
        if serializer_class is None:
            raise NotImplementedError(
                f"{self.__class__.__name__} must define {attribute_name}."
            )
        return serializer_class


class CreateListRetrieveDestroyViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Base viewset for endpoints with create/list/detail/delete actions.

    Subclasses use a write serializer for creates and a read/list serializer for
    list and retrieve responses. Create responses intentionally use the read
    serializer, matching CreateListViewSet.
    """

    permission_classes = [HasAppAccess]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    ordering_fields = [
        "created",
        "modified",
    ]
    ordering = ["-created"]

    create_serializer_class = None
    list_serializer_class = None

    def get_serializer_class(self):
        if self.action == "create":
            return self._get_required_serializer_class("create_serializer_class")
        return self._get_required_serializer_class("list_serializer_class")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        response_serializer_class = self._get_required_serializer_class(
            "list_serializer_class"
        )
        response_serializer = response_serializer_class(
            instance,
            context=self.get_serializer_context(),
        )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    def _get_required_serializer_class(self, attribute_name):
        serializer_class = getattr(self, attribute_name, None)
        if serializer_class is None:
            raise NotImplementedError(
                f"{self.__class__.__name__} must define {attribute_name}."
            )
        return serializer_class

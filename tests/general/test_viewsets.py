import pytest
from rest_framework import serializers

from apps.general.viewsets import CreateListViewSet


class DummySerializer(serializers.Serializer):
    id = serializers.IntegerField()


class DummyCreateListViewSet(CreateListViewSet):
    create_serializer_class = DummySerializer
    list_serializer_class = DummySerializer


def view_for_action(viewset_class, action):
    view = viewset_class()
    view.action = action
    return view


class TestCreateListViewSet:
    def test_uses_create_serializer_for_create_action(self):
        view = view_for_action(DummyCreateListViewSet, "create")

        assert view.get_serializer_class() is DummySerializer

    def test_uses_list_serializer_for_list_action(self):
        view = view_for_action(DummyCreateListViewSet, "list")

        assert view.get_serializer_class() is DummySerializer

    def test_create_serializer_class_is_required_for_create_action(self):
        class MissingCreateSerializerViewSet(CreateListViewSet):
            list_serializer_class = DummySerializer

        view = view_for_action(MissingCreateSerializerViewSet, "create")

        with pytest.raises(
            NotImplementedError,
            match="create_serializer_class",
        ):
            view.get_serializer_class()

    def test_list_serializer_class_is_required_for_list_action(self):
        class MissingListSerializerViewSet(CreateListViewSet):
            create_serializer_class = DummySerializer

        view = view_for_action(MissingListSerializerViewSet, "list")

        with pytest.raises(
            NotImplementedError,
            match="list_serializer_class",
        ):
            view.get_serializer_class()

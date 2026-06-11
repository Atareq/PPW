from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.complaints.models import Complaint
from apps.complaints.serializers import (
    ComplaintAdminActionSerializer,
    ComplaintCreateSerializer,
    ComplaintDetailSerializer,
    ComplaintListSerializer,
)
from apps.complaints.services import ComplaintService, complaint_queryset
from apps.general.authorizations import HasAppAccess
from apps.users.constants import ProfileType


def active_profile(request):
    return request.user.active_profile


@extend_schema_view(
    list=extend_schema(
        tags=["Complaints"],
        responses={200: ComplaintListSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=["Complaints"],
        responses={200: ComplaintDetailSerializer},
    ),
    create=extend_schema(
        tags=["Complaints"],
        request=ComplaintCreateSerializer,
        responses={201: ComplaintDetailSerializer},
    ),
)
class ComplaintViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Create complaints and expose role-scoped complaint reads."""

    permission_classes = [HasAppAccess]
    permission_resource = "COMPLAINT"
    queryset = Complaint.objects.all()
    http_method_names = ["get", "post"]
    allow_scoped_read_access = True
    scope = None

    def get_serializer_class(self):
        if self.action == "create":
            return ComplaintCreateSerializer
        if self.action == "retrieve":
            return ComplaintDetailSerializer
        return ComplaintListSerializer

    def get_queryset(self):
        queryset = complaint_queryset()
        profile = active_profile(self.request)

        if profile.type == ProfileType.MANAGER:
            return queryset

        if self.scope == "my":
            return queryset.filter(buyer_id=profile.pk) | queryset.filter(
                seller_id=profile.pk
            )

        if self.action == "retrieve":
            return queryset.filter(buyer_id=profile.pk) | queryset.filter(
                seller_id=profile.pk
            )

        return queryset.none()

    def list(self, request, *args, **kwargs):
        profile = active_profile(request)
        if self.scope != "my" and profile.type != ProfileType.MANAGER:
            raise PermissionDenied("Only managers can list all complaints.")
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        profile = active_profile(request)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        complaint = ComplaintService.create_complaint(
            serializer.validated_data["payment_request_id"],
            profile,
            title=serializer.validated_data["title"],
            description=serializer.validated_data["description"],
            complaint_type=serializer.validated_data.get("complaint_type"),
        )
        return Response(
            ComplaintDetailSerializer(complaint).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Complaints"],
        request=None,
        responses={200: ComplaintDetailSerializer},
    ),
)
class ComplaintCancelViewSet(viewsets.GenericViewSet):
    """Creator command endpoint for cancelling open complaints."""

    permission_classes = [HasAppAccess]
    permission_resource = "COMPLAINT"
    serializer_class = ComplaintDetailSerializer
    queryset = Complaint.objects.all()
    http_method_names = ["post"]

    def create(self, request, *args, **kwargs):
        complaint = ComplaintService.cancel(
            kwargs["pk"],
            active_profile(request),
        )
        return Response(
            ComplaintDetailSerializer(complaint).data,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Complaints"],
        request=ComplaintAdminActionSerializer,
        responses={200: ComplaintDetailSerializer},
    ),
)
class ComplaintAdminActionViewSet(viewsets.GenericViewSet):
    """Manager command endpoint for complaint review decisions."""

    permission_classes = [HasAppAccess]
    permission_resource = "COMPLAINT_ADMIN"
    serializer_class = ComplaintAdminActionSerializer
    queryset = Complaint.objects.all()
    http_method_names = ["post"]
    action_type = None

    def create(self, request, *args, **kwargs):
        profile = active_profile(request)

        if self.action_type == "mark_in_review":
            complaint = ComplaintService.mark_in_review(kwargs["pk"], profile)
        else:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            admin_note = serializer.validated_data.get("admin_note", "")
            if self.action_type == "resolve":
                complaint = ComplaintService.resolve(
                    kwargs["pk"],
                    profile,
                    admin_note=admin_note,
                )
            else:
                complaint = ComplaintService.reject(
                    kwargs["pk"],
                    profile,
                    admin_note=admin_note,
                )

        return Response(
            ComplaintDetailSerializer(complaint).data,
            status=status.HTTP_200_OK,
        )

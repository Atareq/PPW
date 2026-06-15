from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.pedigrees.models import PedigreeDocument, PedigreeDocumentStatus
from apps.pedigrees.serializers import (
    PedigreeDocumentDetailSerializer,
    PedigreeDocumentReviewSerializer,
    PedigreeDocumentUploadSerializer,
)
from apps.pedigrees.services import (
    PedigreeDocumentService,
    pedigree_document_queryset,
)
from apps.users.constants import ProfileType


@extend_schema_view(
    create=extend_schema(
        tags=["Pedigrees"],
        request=PedigreeDocumentUploadSerializer,
        responses={201: PedigreeDocumentDetailSerializer},
    ),
    retrieve=extend_schema(
        tags=["Pedigrees"],
        responses={200: PedigreeDocumentDetailSerializer},
    ),
)
class PedigreeDocumentViewSet(
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Upload and retrieve profile-owned pedigree OCR documents."""

    permission_classes = [HasAppAccess]
    permission_resource = "PEDIGREE_DOCUMENT"
    allow_scoped_read_access = True
    queryset = PedigreeDocument.objects.all()
    http_method_names = ["get", "post"]

    def get_serializer_class(self):
        if self.action == "create":
            return PedigreeDocumentUploadSerializer
        return PedigreeDocumentDetailSerializer

    def get_queryset(self):
        queryset = pedigree_document_queryset()
        active_profile = self.request.user.active_profile
        if active_profile.type == ProfileType.MANAGER:
            return queryset
        return queryset.filter(owner_id=active_profile.pk)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = PedigreeDocumentService.upload_and_process(
            uploaded_by=request.user,
            owner=request.user.active_profile,
            original_file=serializer.validated_data["original_file"],
            bird_id=serializer.validated_data.get("bird_id"),
        )
        response_serializer = PedigreeDocumentDetailSerializer(
            document,
            context=self.get_serializer_context(),
        )
        if document.status == PedigreeDocumentStatus.FAILED:
            return Response(
                {
                    "detail": document.processing_error,
                    "document": response_serializer.data,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    partial_update=extend_schema(
        tags=["Pedigrees"],
        request=PedigreeDocumentReviewSerializer,
        responses={200: PedigreeDocumentDetailSerializer},
    ),
)
class PedigreeDocumentReviewViewSet(
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Review corrected OCR extraction fields for one document."""

    permission_classes = [HasAppAccess]
    permission_resource = "PEDIGREE_DOCUMENT"
    serializer_class = PedigreeDocumentReviewSerializer
    queryset = PedigreeDocument.objects.all()
    http_method_names = ["patch"]

    def get_queryset(self):
        queryset = pedigree_document_queryset()
        active_profile = self.request.user.active_profile
        if active_profile.type == ProfileType.MANAGER:
            return queryset
        return queryset.filter(owner_id=active_profile.pk)

    def partial_update(self, request, *args, **kwargs):
        document = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reviewed_document = PedigreeDocumentService.review_document(
            document_id=document.pk,
            actor_profile=request.user.active_profile,
            bird_ring_number=serializer.validated_data["bird_ring_number"],
            description=serializer.validated_data["description"],
        )
        return Response(
            PedigreeDocumentDetailSerializer(
                reviewed_document,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_200_OK,
        )

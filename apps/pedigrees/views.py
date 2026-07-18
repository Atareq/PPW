from django.http import FileResponse
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.pedigrees.filters import PedigreeBirdFilter
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeCertificate,
    PedigreeCertificateStatus,
    PedigreeDocument,
    PedigreeDocumentStatus,
    PedigreeTemplate,
)
from apps.pedigrees.serializers import (
    PedigreeBirdSerializer,
    PedigreeCertificateCreateResponseSerializer,
    PedigreeCertificateCreateSerializer,
    PedigreeCertificateDetailSerializer,
    PedigreeCertificateRegenerateSerializer,
    PedigreeDocumentDetailSerializer,
    PedigreeDocumentReviewResponseSerializer,
    PedigreeDocumentReviewSerializer,
    PedigreeDocumentUploadSerializer,
    PedigreeTemplateListSerializer,
)
from apps.pedigrees.services import (
    PedigreeDocumentService,
    PedigreeTemplateAccessService,
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
    manager_permission_map = {
        "list": "pedigrees.documents.list",
        "retrieve": "pedigrees.documents.retrieve",
    }
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
        responses={200: PedigreeDocumentReviewResponseSerializer},
    ),
)
class PedigreeDocumentReviewViewSet(
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Review corrected OCR extraction fields for one document."""

    permission_classes = [HasAppAccess]
    permission_resource = "PEDIGREE_DOCUMENT"
    manager_permission_map = {
        "partial_update": "pedigrees.documents.review",
    }
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
        result = PedigreeDocumentService.review_document(
            document_id=document.pk,
            actor_profile=request.user.active_profile,
            birds=serializer.validated_data["birds"],
        )
        return Response(
            PedigreeDocumentReviewResponseSerializer(
                {
                    "document": result.document,
                    "subject_bird_id": result.subject_bird_id,
                    "path_to_bird_id": result.path_to_bird_id,
                    "created_count": result.created_count,
                    "reused_count": result.reused_count,
                    "updated_count": result.updated_count,
                },
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Pedigrees"], responses={200: PedigreeBirdSerializer}
    ),
    create=extend_schema(
        tags=["Pedigrees"],
        request=PedigreeBirdSerializer,
        responses={201: PedigreeBirdSerializer},
    ),
    retrieve=extend_schema(
        tags=["Pedigrees"], responses={200: PedigreeBirdSerializer}
    ),
    partial_update=extend_schema(
        tags=["Pedigrees"],
        request=PedigreeBirdSerializer,
        responses={200: PedigreeBirdSerializer},
    ),
)
class PedigreeBirdViewSet(viewsets.ModelViewSet):
    """Private profile-owned pedigree bird CRUD, independent from assets.Bird."""

    permission_classes = [HasAppAccess]
    permission_resource = "PEDIGREE_BIRD"
    manager_permission_map = {
        "list": "pedigrees.birds.list",
        "retrieve": "pedigrees.birds.retrieve",
        "create": "pedigrees.birds.create",
        "update": "pedigrees.birds.update",
        "partial_update": "pedigrees.birds.update",
        "destroy": "pedigrees.birds.delete",
    }
    serializer_class = PedigreeBirdSerializer
    queryset = PedigreeBird.objects.all()
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = PedigreeBirdFilter
    ordering_fields = [
        "created",
        "modified",
        "ring_number",
        "name",
        "birthday",
        "record_date",
    ]

    def get_queryset(self):
        queryset = PedigreeBird.objects.select_related(
            "owner",
            "father",
            "mother",
        ).order_by("-created")
        active_profile = self.request.user.active_profile
        if active_profile.type == ProfileType.MANAGER:
            return queryset
        return queryset.filter(owner_id=active_profile.pk)


@extend_schema_view(
    list=extend_schema(
        tags=["Pedigrees"],
        responses={200: PedigreeTemplateListSerializer},
    ),
)
class PedigreeTemplateViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """List configured pedigree templates without exposing provider internals."""

    permission_classes = [HasAppAccess]
    permission_resource = "PEDIGREE_TEMPLATE"
    manager_permission_map = {"list": "pedigrees.templates.list"}
    serializer_class = PedigreeTemplateListSerializer
    queryset = PedigreeTemplate.objects.all()

    def get_queryset(self):
        return PedigreeTemplateAccessService.available_templates_for_profile(
            self.request.user.active_profile
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Pedigrees"],
        responses={200: PedigreeCertificateDetailSerializer},
    ),
    create=extend_schema(
        tags=["Pedigrees"],
        request=PedigreeCertificateCreateSerializer,
        responses={202: PedigreeCertificateCreateResponseSerializer},
    ),
    retrieve=extend_schema(
        tags=["Pedigrees"],
        responses={200: PedigreeCertificateDetailSerializer},
    ),
)
class PedigreeCertificateViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Create and retrieve generated pedigree certificate records."""

    permission_classes = [HasAppAccess]
    permission_resource = "PEDIGREE_CERTIFICATE"
    manager_permission_map = {
        "list": "pedigrees.certificates.list",
        "retrieve": "pedigrees.certificates.retrieve",
        "create": "pedigrees.certificates.generate",
    }
    queryset = PedigreeCertificate.objects.all()

    def get_serializer_class(self):
        if self.action == "create":
            return PedigreeCertificateCreateSerializer
        return PedigreeCertificateDetailSerializer

    def get_queryset(self):
        queryset = PedigreeCertificate.objects.select_related(
            "owner",
            "requested_by",
            "subject_bird",
            "template",
        ).order_by("-created")
        active_profile = self.request.user.active_profile
        if active_profile.type == ProfileType.MANAGER:
            return queryset
        return queryset.filter(owner_id=active_profile.pk)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        certificate = serializer.save()
        return Response(
            PedigreeCertificateCreateResponseSerializer(certificate).data,
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema_view(
    create=extend_schema(
        tags=["Pedigrees"],
        request=PedigreeCertificateRegenerateSerializer,
        responses={202: PedigreeCertificateCreateResponseSerializer},
    ),
)
class PedigreeCertificateRegenerateViewSet(
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Regenerate one certificate from edited canonical data."""

    permission_classes = [HasAppAccess]
    permission_resource = "PEDIGREE_CERTIFICATE"
    manager_permission_map = {"create": "pedigrees.certificates.generate"}
    serializer_class = PedigreeCertificateRegenerateSerializer
    queryset = PedigreeCertificate.objects.all()

    def get_queryset(self):
        return PedigreeCertificateViewSet.get_queryset(self)

    def create(self, request, *args, **kwargs):
        certificate = self.get_object()
        serializer = self.get_serializer(
            data=request.data,
            context={
                **self.get_serializer_context(),
                "certificate": certificate,
            },
        )
        serializer.is_valid(raise_exception=True)
        regenerated = serializer.save()
        return Response(
            PedigreeCertificateCreateResponseSerializer(regenerated).data,
            status=status.HTTP_202_ACCEPTED,
        )


class PedigreeCertificateDownloadViewSet(
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Download the completed generated certificate file."""

    permission_classes = [HasAppAccess]
    permission_resource = "PEDIGREE_CERTIFICATE"
    serializer_class = PedigreeCertificateDetailSerializer
    queryset = PedigreeCertificate.objects.all()

    def get_required_manager_permission(self):
        return "pedigrees.certificates.download"

    def get_queryset(self):
        return PedigreeCertificateViewSet.get_queryset(self)

    def retrieve(self, request, *args, **kwargs):
        certificate = self.get_object()
        if (
            certificate.status != PedigreeCertificateStatus.COMPLETED
            or not certificate.generated_file
        ):
            raise ValidationError(
                {
                    "generated_file": "Generated certificate file is unavailable."
                }
            )
        response = FileResponse(
            certificate.generated_file.open("rb"),
            content_type=certificate.mime_type or "application/octet-stream",
        )
        filename = certificate.generated_filename or "pedigree-certificate"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

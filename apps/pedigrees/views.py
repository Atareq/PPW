import logging

from django.http import HttpResponse, JsonResponse
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
import requests
from rest_framework import mixins, status, viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response

from apps.general.authorizations import HasAppAccess
from apps.pedigrees.filters import PedigreeBirdFilter
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeDocument,
    PedigreeDocumentStatus,
)
from apps.pedigrees.serializers import (
    PedigreeBirdSerializer,
    PedigreeDocumentDetailSerializer,
    PedigreeDocumentReviewResponseSerializer,
    PedigreeDocumentReviewSerializer,
    PedigreeDocumentUploadSerializer,
    PedigreeReportGenerateSerializer,
)
from apps.pedigrees.services import (
    PedigreeDocumentService,
    PedigreeReportClient,
    PedigreeReportGenerationService,
    pedigree_document_queryset,
)
from apps.users.constants import ProfileType

logger = logging.getLogger(__name__)


class ProviderProxyRenderer(BaseRenderer):
    media_type = "*/*"
    format = "proxy"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        return JSONRenderer().render(
            data,
            accepted_media_type="application/json",
            renderer_context=renderer_context,
        )


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
        responses={200: OpenApiTypes.BINARY},
        description=(
            "Proxy the external FastReport reports list. PPW passes through "
            "the upstream status, body, Content-Type, and safe response "
            "headers without storing or reshaping the report definitions."
        ),
    ),
)
class PedigreeReportListProxyViewSet(
    mixins.ListModelMixin, viewsets.GenericViewSet
):
    """Proxy the external reports list without local template persistence."""

    permission_classes = [HasAppAccess]
    permission_resource = "PEDIGREE_REPORT"
    manager_permission_map = {"list": "pedigrees.reports.list"}
    renderer_classes = [JSONRenderer, ProviderProxyRenderer]
    queryset = PedigreeBird.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            upstream_response = PedigreeReportClient().get_reports()
        except requests.exceptions.Timeout as exc:
            _log_provider_exception("reports-list", exc)
            return _provider_timeout_response()
        except requests.exceptions.ConnectionError as exc:
            _log_provider_exception("reports-list", exc)
            return _provider_gateway_response()
        return _raw_provider_response(upstream_response)


@extend_schema_view(
    create=extend_schema(
        tags=["Pedigrees"],
        request=PedigreeReportGenerateSerializer,
        responses={200: OpenApiTypes.BINARY},
        description=(
            "Build a provider payload from the frontend report object and the "
            "current PedigreeBird graph, call the external FastReport PDF "
            "endpoint synchronously, and pass through the upstream response."
        ),
    ),
)
class PedigreeReportGenerateViewSet(
    mixins.CreateModelMixin, viewsets.GenericViewSet
):
    """Synchronously generate one external report without local persistence."""

    permission_classes = [HasAppAccess]
    permission_resource = "PEDIGREE_REPORT"
    manager_permission_map = {"create": "pedigrees.reports.generate"}
    serializer_class = PedigreeReportGenerateSerializer
    renderer_classes = [JSONRenderer, ProviderProxyRenderer]
    queryset = PedigreeBird.objects.all()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        provider_payload = (
            PedigreeReportGenerationService.build_provider_payload(
                subject_bird_id=data["subject_bird_id"],
                actor_profile=request.user.active_profile,
                generation_depth=data["generation_depth"],
                report=data["report"],
                owner=data.get("owner") or {},
                customization=data.get("customization") or {},
            )
        )
        try:
            upstream_response = PedigreeReportClient().post_pdf(
                pdf_id=data["pdf_id"],
                payload=provider_payload,
                accept=request.headers.get("Accept") or "application/pdf",
            )
        except requests.exceptions.Timeout as exc:
            _log_provider_exception(
                "generate",
                exc,
                subject_bird_id=data["subject_bird_id"],
                pdf_id=data["pdf_id"],
                generation_depth=data["generation_depth"],
            )
            return _provider_timeout_response()
        except requests.exceptions.ConnectionError as exc:
            _log_provider_exception(
                "generate",
                exc,
                subject_bird_id=data["subject_bird_id"],
                pdf_id=data["pdf_id"],
                generation_depth=data["generation_depth"],
            )
            return _provider_gateway_response()
        return _raw_provider_response(upstream_response)


def _raw_provider_response(upstream_response):
    response = HttpResponse(
        upstream_response.content,
        status=upstream_response.status_code,
        content_type=upstream_response.headers.get(
            "Content-Type", "application/octet-stream"
        ),
    )
    for header in PedigreeReportClient.SAFE_RESPONSE_HEADERS:
        if header in upstream_response.headers:
            response[header] = upstream_response.headers[header]
    return response


def _provider_timeout_response():
    return JsonResponse(
        {"detail": "Pedigree report provider timed out."},
        status=504,
    )


def _provider_gateway_response():
    return JsonResponse(
        {"detail": "Pedigree report provider was unavailable."},
        status=502,
    )


def _log_provider_exception(operation, exc, **extra):
    logger.warning(
        "Pedigree report provider request failed.",
        extra={
            "operation": operation,
            "exception_type": exc.__class__.__name__,
            **extra,
        },
    )

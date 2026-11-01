from django.urls import path

from apps.pedigrees.views import (
    PedigreeBirdOwnershipViewSet,
    PedigreeBirdViewSet,
    PedigreeDocumentReviewViewSet,
    PedigreeDocumentViewSet,
    PedigreeProviderTestViewSet,
    PedigreeReportGenerateViewSet,
    PedigreeReportListProxyViewSet,
    PedigreeReportSchemaProxyViewSet,
    PedigreeReportStyleParametersProxyViewSet,
    PedigreeSpreadsheetImportViewSet,
    RegisteredPedigreeViewSet,
)

CREATE_ACTION = {"post": "create"}
LIST_CREATE_ACTION = {"get": "list", "post": "create"}
LIST_ACTION = {"get": "list"}
RETRIEVE_ACTION = {"get": "retrieve"}
RETRIEVE_UPDATE_DESTROY_ACTION = {
    "get": "retrieve",
    "patch": "partial_update",
    "delete": "destroy",
}
PARTIAL_UPDATE_ACTION = {"patch": "partial_update"}

app_name = "pedigrees"

urlpatterns = [
    path(
        "birds/",
        PedigreeBirdViewSet.as_view(LIST_CREATE_ACTION),
        name="pedigree-bird-list",
    ),
    path(
        "birds/import/",
        PedigreeSpreadsheetImportViewSet.as_view(CREATE_ACTION),
        name="pedigree-bird-import",
    ),
    path(
        "birds/<int:pk>/",
        PedigreeBirdViewSet.as_view(RETRIEVE_UPDATE_DESTROY_ACTION),
        name="pedigree-bird-detail",
    ),
    path(
        "birds/public/<uuid:public_id>/claim/",
        PedigreeBirdOwnershipViewSet.as_view(CREATE_ACTION, command="claim"),
        name="pedigree-bird-claim",
    ),
    path(
        "birds/public/<uuid:public_id>/release/",
        PedigreeBirdOwnershipViewSet.as_view(CREATE_ACTION, command="release"),
        name="pedigree-bird-release",
    ),
    path(
        "registered/",
        RegisteredPedigreeViewSet.as_view(LIST_CREATE_ACTION),
        name="registered-pedigree-list",
    ),
    path(
        "registered/<int:pk>/",
        RegisteredPedigreeViewSet.as_view(RETRIEVE_UPDATE_DESTROY_ACTION),
        name="registered-pedigree-detail",
    ),
    path(
        "available/",
        RegisteredPedigreeViewSet.as_view(LIST_ACTION),
        name="available-pedigree-list",
    ),
    path(
        "available/<int:pedigree_id>/",
        RegisteredPedigreeViewSet.as_view(RETRIEVE_ACTION, lookup_field="pedigree_id"),
        name="available-pedigree-detail",
    ),
    path(
        "templates/",
        PedigreeReportListProxyViewSet.as_view(LIST_ACTION),
        name="pedigree-template-list",
    ),
    path(
        "templates/<int:shape>/schema/",
        PedigreeReportSchemaProxyViewSet.as_view(LIST_ACTION),
        name="pedigree-template-schema",
    ),
    path(
        "templates/<int:shape>/style-parameters/",
        PedigreeReportStyleParametersProxyViewSet.as_view(LIST_ACTION),
        name="pedigree-template-style-parameters",
    ),
    path(
        "generate/",
        PedigreeReportGenerateViewSet.as_view(CREATE_ACTION),
        name="pedigree-report-generate",
    ),
    path(
        "provider-test/",
        PedigreeProviderTestViewSet.as_view(CREATE_ACTION),
        name="pedigree-provider-test",
    ),
    path(
        "documents/",
        PedigreeDocumentViewSet.as_view(CREATE_ACTION),
        name="pedigree-document-upload",
    ),
    path(
        "documents/<int:pk>/",
        PedigreeDocumentViewSet.as_view(RETRIEVE_ACTION),
        name="pedigree-document-detail",
    ),
    path(
        "documents/<int:pk>/review/",
        PedigreeDocumentReviewViewSet.as_view(PARTIAL_UPDATE_ACTION),
        name="pedigree-document-review",
    ),
]

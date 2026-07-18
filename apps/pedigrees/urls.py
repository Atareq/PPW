from django.urls import path

from apps.pedigrees.views import (
    PedigreeBirdViewSet,
    PedigreeCertificateDownloadViewSet,
    PedigreeCertificateRegenerateViewSet,
    PedigreeCertificateViewSet,
    PedigreeDocumentReviewViewSet,
    PedigreeDocumentViewSet,
    PedigreeTemplateViewSet,
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
        "birds/<int:pk>/",
        PedigreeBirdViewSet.as_view(RETRIEVE_UPDATE_DESTROY_ACTION),
        name="pedigree-bird-detail",
    ),
    path(
        "templates/",
        PedigreeTemplateViewSet.as_view(LIST_ACTION),
        name="pedigree-template-list",
    ),
    path(
        "certificates/",
        PedigreeCertificateViewSet.as_view(LIST_CREATE_ACTION),
        name="pedigree-certificate-list",
    ),
    path(
        "certificates/<int:pk>/",
        PedigreeCertificateViewSet.as_view(RETRIEVE_ACTION),
        name="pedigree-certificate-detail",
    ),
    path(
        "certificates/<int:pk>/regenerate/",
        PedigreeCertificateRegenerateViewSet.as_view(CREATE_ACTION),
        name="pedigree-certificate-regenerate",
    ),
    path(
        "certificates/<int:pk>/download/",
        PedigreeCertificateDownloadViewSet.as_view(RETRIEVE_ACTION),
        name="pedigree-certificate-download",
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

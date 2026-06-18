from django.urls import path

from apps.pedigrees.views import (
    PedigreeDocumentReviewViewSet,
    PedigreeDocumentViewSet,
)

LIST_CREATE_ACTION = {"get": "list", "post": "create"}
RETRIEVE_ACTION = {"get": "retrieve"}
PARTIAL_UPDATE_ACTION = {"patch": "partial_update"}

app_name = "pedigrees"

urlpatterns = [
    path(
        "documents/",
        PedigreeDocumentViewSet.as_view(LIST_CREATE_ACTION),
        name="pedigree-document-list-upload",
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

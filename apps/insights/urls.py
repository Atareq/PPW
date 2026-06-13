from django.urls import path

from apps.insights.views import SellerHomeInsightsViewSet

LIST_ACTION = {"get": "list"}

app_name = "insights"

urlpatterns = [
    path(
        "seller/home/",
        SellerHomeInsightsViewSet.as_view(LIST_ACTION),
        name="seller-home-summary",
    ),
]

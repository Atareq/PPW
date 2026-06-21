"""
URL configuration for ppw project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.urls import include, path

from ppw.schema import InternalSchemaAPIView, InternalSwaggerUIView

urlpatterns = [
    path("api/auth/", include("apps.authentication.urls")),
    path("api/users/", include("apps.users.urls")),
    path("api/assets/", include("apps.assets.urls")),
    path("api/auctions/", include("apps.auctions.urls")),
    path("api/orders/", include("apps.orders.urls")),
    path("api/", include("apps.ratings.urls")),
    path("api/", include("apps.races.urls")),
    path("api/", include("apps.packages.urls")),
    path("api/loyalty/", include("apps.loyalty.urls")),
    path("api/notifications/", include("apps.notifications.urls")),
    path("api/payments/", include("apps.payments.urls")),
    path("api/promotions/", include("apps.promotions.urls")),
    path("api/complaints/", include("apps.complaints.urls")),
    path("api/chat/", include("apps.chat.urls")),
    path("api/feed/", include("apps.feed.urls")),
    path("api/insights/", include("apps.insights.urls")),
    path("api/pedigrees/", include("apps.pedigrees.urls")),
    path(
        "api/schema/",
        InternalSchemaAPIView.as_view(),
        name="api-schema",
    ),
]

if settings.DEBUG:
    urlpatterns += [
        path(
            "api/docs/swagger/",
            InternalSwaggerUIView.as_view(url_name="api-schema"),
            name="swagger-ui",
        ),
    ]

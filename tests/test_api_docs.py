import pytest
import yaml

pytestmark = pytest.mark.django_db


def load_schema(response):
    assert response.status_code == 200
    return yaml.safe_load(response.content.decode())


def test_schema_requires_auth_in_non_debug(
    api_client,
    authenticated_client,
    settings,
):
    settings.DEBUG = False

    anonymous_response = api_client.get("/api/schema/")
    authenticated_response = authenticated_client.get("/api/schema/")

    assert anonymous_response.status_code == 401
    schema = load_schema(authenticated_response)
    assert schema["openapi"] == "3.0.3"


def test_schema_exposes_bearer_auth_for_protected_operations(
    authenticated_client,
    settings,
):
    settings.DEBUG = False

    schema = load_schema(authenticated_client.get("/api/schema/"))

    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert schema["paths"]["/api/auth/login/"]["post"].get("security") is None
    assert {"BearerAuth": []} in schema["paths"]["/api/orders/"]["get"][
        "security"
    ]


def test_manual_order_item_approve_endpoint_is_documented(
    authenticated_client,
    settings,
):
    settings.DEBUG = False

    schema = load_schema(authenticated_client.get("/api/schema/"))
    approve_path = next(
        path
        for path in schema["paths"]
        if path.startswith("/api/orders/items/{")
        and path.endswith("/approve/")
    )
    operation = schema["paths"][approve_path]["post"]

    assert "requestBody" not in operation
    assert {"BearerAuth": []} in operation["security"]
    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/OrderItem"}


def test_place_bid_custom_action_is_documented(
    authenticated_client,
    settings,
):
    settings.DEBUG = False

    schema = load_schema(authenticated_client.get("/api/schema/"))
    place_bid_path = next(
        path
        for path in schema["paths"]
        if path.startswith("/api/auctions/items/{")
        and path.endswith("/place_bid/")
    )
    operation = schema["paths"][place_bid_path]["post"]

    assert {"BearerAuth": []} in operation["security"]
    assert operation["requestBody"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/PlaceBidRequest"}


def test_cart_and_checkout_paths_are_documented(
    authenticated_client,
    settings,
):
    settings.DEBUG = False

    schema = load_schema(authenticated_client.get("/api/schema/"))

    assert "/api/orders/cart/" in schema["paths"]
    assert "/api/orders/cart/items/" in schema["paths"]
    assert "/api/orders/cart/items/{id}/" in schema["paths"]
    assert "/api/orders/cart/clear/" in schema["paths"]
    assert "/api/orders/checkout/" in schema["paths"]
    assert schema["paths"]["/api/orders/cart/"]["get"]["tags"] == ["Cart"]
    assert schema["paths"]["/api/orders/cart/items/"]["post"]["tags"] == [
        "Cart"
    ]
    assert schema["paths"]["/api/orders/checkout/"]["post"]["tags"] == [
        "Checkout"
    ]
    assert {"BearerAuth": []} in schema["paths"]["/api/orders/checkout/"][
        "post"
    ]["security"]

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
import pytest
from rest_framework import status

from apps.authentication.models import UserAccount
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import UserAccountFactory

pytestmark = pytest.mark.django_db

CUSTOMER_PROFILE_URL = "/api/users/mobile/customers/"
SELLER_PROFILE_URL = "/api/users/mobile/sellers/"
CUSTOMER_REGISTER_URL = "/api/auth/register/customer/"
CUSTOMER_LOGIN_URL = "/api/auth/login/"


def customer_registration_payload():
    return {
        "username": "newcustomer",
        "phone_number": "+201009998877",
        "password": "SecurePass123!@",
        "customer_profile": {
            "country": "EG",
            "currency": "EGP",
        },
    }


def loft_picture_file(name="loft.jpg"):
    image = Image.new("RGB", (1, 1), color="red")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="image/jpeg")


def test_existing_user_remains_valid_without_secondary_phone_number(customer_user):
    customer_user.refresh_from_db()

    assert customer_user.secondary_phone_number is None
    assert customer_user.phone_number is not None


def test_customer_can_store_and_update_secondary_phone_number(
    authenticated_customer_client, customer_user
):
    customer_profile = CustomerProfile.objects.get(user=customer_user)

    create_response = authenticated_customer_client.patch(
        f"{CUSTOMER_PROFILE_URL}{customer_profile.id}/",
        {"secondary_phone_number": "+201112223344"},
        format="json",
    )

    assert create_response.status_code == status.HTTP_200_OK
    assert create_response.data["secondary_phone_number"] == "+201112223344"

    customer_user.refresh_from_db()
    assert str(customer_user.secondary_phone_number) == "+201112223344"

    update_response = authenticated_customer_client.patch(
        f"{CUSTOMER_PROFILE_URL}{customer_profile.id}/",
        {"secondary_phone_number": "+201115556677"},
        format="json",
    )

    assert update_response.status_code == status.HTTP_200_OK
    assert update_response.data["secondary_phone_number"] == "+201115556677"

    customer_user.refresh_from_db()
    assert str(customer_user.secondary_phone_number) == "+201115556677"


def test_existing_profile_remains_valid_without_loft_fields(customer_user):
    customer_profile = CustomerProfile.objects.get(user=customer_user)

    assert customer_profile.loft_name == ""
    assert not customer_profile.loft_picture


def test_customer_can_store_and_update_loft_name(
    authenticated_customer_client, customer_user
):
    customer_profile = CustomerProfile.objects.get(user=customer_user)

    response = authenticated_customer_client.patch(
        f"{CUSTOMER_PROFILE_URL}{customer_profile.id}/",
        {"loft_name": "Sunrise Loft"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["loft_name"] == "Sunrise Loft"

    customer_profile.refresh_from_db()
    assert customer_profile.loft_name == "Sunrise Loft"

    update_response = authenticated_customer_client.patch(
        f"{CUSTOMER_PROFILE_URL}{customer_profile.id}/",
        {"loft_name": "Evening Loft"},
        format="json",
    )

    assert update_response.status_code == status.HTTP_200_OK
    assert update_response.data["loft_name"] == "Evening Loft"

    customer_profile.refresh_from_db()
    assert customer_profile.loft_name == "Evening Loft"


def test_seller_can_upload_and_update_loft_picture(
    authenticated_seller_client, seller_user
):
    seller_profile = SellerProfile.objects.get(user=seller_user)

    upload_response = authenticated_seller_client.patch(
        f"{SELLER_PROFILE_URL}{seller_profile.id}/",
        {
            "loft_name": "Racing Loft",
            "loft_picture": loft_picture_file(),
        },
        format="multipart",
    )

    assert upload_response.status_code == status.HTTP_200_OK
    assert upload_response.data["loft_name"] == "Racing Loft"
    assert upload_response.data["loft_picture"].endswith(".jpg")

    seller_profile.refresh_from_db()
    assert seller_profile.loft_name == "Racing Loft"
    assert seller_profile.loft_picture.name.startswith("lofts/")

    replacement_response = authenticated_seller_client.patch(
        f"{SELLER_PROFILE_URL}{seller_profile.id}/",
        {"loft_picture": loft_picture_file("replacement-loft.jpg")},
        format="multipart",
    )

    assert replacement_response.status_code == status.HTTP_200_OK
    assert "replacement-loft" in replacement_response.data["loft_picture"]

    seller_profile.refresh_from_db()
    assert "replacement-loft" in seller_profile.loft_picture.name


def test_registration_and_login_do_not_require_new_fields(api_client):
    payload = customer_registration_payload()

    register_response = api_client.post(
        CUSTOMER_REGISTER_URL,
        payload,
        format="json",
    )

    assert register_response.status_code == status.HTTP_201_CREATED

    user = UserAccount.objects.get(phone_number=payload["phone_number"])
    assert user.secondary_phone_number is None
    assert user.active_profile.loft_name == ""
    assert not user.active_profile.loft_picture

    login_response = api_client.post(
        CUSTOMER_LOGIN_URL,
        {
            "phone_number": payload["phone_number"],
            "password": payload["password"],
        },
        format="json",
    )

    assert login_response.status_code == status.HTTP_200_OK
    assert "access" in login_response.data
    assert "refresh" in login_response.data

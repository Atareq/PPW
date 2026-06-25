from rest_framework.exceptions import ValidationError

ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


def validate_image_file(file):
    """Reject uploads that are not jpg/jpeg/png/webp by extension and content-type."""

    name = getattr(file, "name", "") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file type '.{ext}'. Allowed: jpg, jpeg, png, webp."
        )

    content_type = getattr(file, "content_type", None)
    if content_type and content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise ValidationError(
            f"Unsupported content type '{content_type}'. Must be an image."
        )

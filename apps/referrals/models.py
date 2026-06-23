from django.db import models

from apps.general.models import BaseModel


class LinkSharingCode(BaseModel):
    owner_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="referral_codes",
    )
    code = models.CharField(max_length=32, unique=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    used_by_profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="used_referral_codes",
        null=True,
        blank=True,
    )
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(
                fields=["owner_profile", "-created"], name="ref_owner_created_idx"
            ),
            models.Index(fields=["code"], name="ref_code_idx"),
            models.Index(fields=["expires_at"], name="ref_expires_idx"),
        ]

    def __str__(self):
        return self.code

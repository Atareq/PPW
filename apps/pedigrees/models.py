import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel, OwnerBaseModel
from apps.pedigrees.constants import PEDIGREE_MAX_GENERATION_DEPTH


class PedigreeBirdSex(models.TextChoices):
    MALE = "male", _("Male")
    FEMALE = "female", _("Female")
    UNKNOWN = "unknown", _("Unknown")


class PedigreeBirdColour(models.TextChoices):
    WHITE = "white", _("White")
    GRIZZLE = "grizzle", _("Grizzle")
    BLUE_BAR_SPLASH = "blue_bar_splash", _("Blue Bar Splash")
    BLUE_BAR_PIED = "blue_bar_pied", _("Blue Bar Pied")
    BLUE_BAR = "blue_bar", _("Blue Bar")
    BLUE_BAR_WHITE_FLIGHT = "blue_bar_white_flight", _("Blue Bar White Flight")
    PENCIL_BLUE_BAR_PIED = "pencil_blue_bar_pied", _("Pencil Blue Bar Pied")
    BLUE_CHECK_PIED = "blue_check_pied", _("Blue Check Pied")
    FADED_BLUE_CHECK_WHITE_FLIGHT = (
        "faded_blue_check_white_flight",
        _("Faded Blue Check White Flight"),
    )
    BLUE_CHECK_WHITE_FLIGHT = "blue_check_white_flight", _(
        "Blue Check White Flight"
    )
    BLUE_CHECK = "blue_check", _("Blue Check")
    DARK_CHECK = "dark_check", _("Dark Check")
    SLATE_BLUE_BAR = "slate_blue_bar", _("Slate Blue Bar")
    BLACK_CHECK = "black_check", _("Black Check")
    BLACK = "black", _("Black")
    BLACK_CHECK_PIED = "black_check_pied", _("Black Check Pied")
    DARK_CHECK_PIED = "dark_check_pied", _("Dark Check Pied")
    TIGER_GRIZZLE = "tiger_grizzle", _("Tiger Grizzle")
    RED_GRIZZLE = "red_grizzle", _("Red Grizzle")
    RECESSIVE_RED = "recessive_red", _("Recessive Red")
    DARK_RED_CHECK = "dark_red_check", _("Dark Red Check")
    FADED_ASH_RED_CHECK = "faded_ash_red_check", _("Faded Ash Red Check")
    RED_CHECK_PIED = "red_check_pied", _("Red Check Pied")
    RED_CHECK = "red_check", _("Red Check")
    ASH_RED_BAR_SLATE = "ash_red_bar_slate", _("Ash Red Bar Slate")
    RED_BAR = "red_bar", _("Red Bar")
    RED_BAR_PIED = "red_bar_pied", _("Red Bar Pied")
    ASH_RED_BAR_SILVER = "ash_red_bar_silver", _("Ash Red Bar-Silver")
    UNKNOWN = "unknown", _("Unknown")
    OTHER = "other", _("Other")


class PedigreeBirdLifeStatus(models.TextChoices):
    ALIVE = "alive", _("Alive")
    DECEASED = "deceased", _("Deceased")
    MISSING = "missing", _("Missing")


class PedigreeBirdOwnershipStatus(models.TextChoices):
    OWNED = "owned", _("Owned")
    SOLD = "sold", _("Sold")
    GIFTED = "gifted", _("Gifted")


class PedigreeBirdActivityStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    INACTIVE = "inactive", _("Inactive")
    IN_LOFT_RACE = "in_loft_race", _("In Loft Race")
    MISSING_IN_LOFT_RACE = "missing_in_loft_race", _("Missing In Loft Race")


class PedigreeBirdJob(models.TextChoices):
    NONE = "none", _("None")
    RACER = "racer", _("Racer")
    BREEDER = "breeder", _("Breeder")
    FOSTER = "foster", _("Foster")


def normalize_ring_number(value):
    return " ".join((value or "").strip().upper().split())


class PedigreeBird(OwnerBaseModel):
    public_id = models.UUIDField(
        unique=True, default=uuid.uuid4, editable=False
    )
    ring_number = models.CharField(max_length=100)
    normalized_ring_number = models.CharField(max_length=100, editable=False)
    ring_code = models.CharField(max_length=50, blank=True)
    name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    record_date = models.DateField(null=True, blank=True)
    birthday = models.DateField(null=True, blank=True)
    sex = models.CharField(
        max_length=10,
        choices=PedigreeBirdSex.choices,
        default=PedigreeBirdSex.UNKNOWN,
    )
    colour = models.CharField(
        max_length=40,
        choices=PedigreeBirdColour.choices,
        default=PedigreeBirdColour.UNKNOWN,
    )
    life_status = models.CharField(
        max_length=20,
        choices=PedigreeBirdLifeStatus.choices,
        default=PedigreeBirdLifeStatus.ALIVE,
    )
    ownership_status = models.CharField(
        max_length=20,
        choices=PedigreeBirdOwnershipStatus.choices,
        default=PedigreeBirdOwnershipStatus.OWNED,
    )
    activity_status = models.CharField(
        max_length=30,
        choices=PedigreeBirdActivityStatus.choices,
        default=PedigreeBirdActivityStatus.ACTIVE,
    )
    job = models.CharField(
        max_length=20,
        choices=PedigreeBirdJob.choices,
        default=PedigreeBirdJob.NONE,
    )
    external_owner_name = models.CharField(max_length=255, blank=True)
    father = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pedigree_children_from_father",
    )
    mother = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pedigree_children_from_mother",
    )

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["owner"], name="ped_bird_owner_idx"),
            models.Index(
                fields=["normalized_ring_number"],
                name="ped_bird_norm_ring_idx",
            ),
            models.Index(
                fields=["owner", "normalized_ring_number"],
                name="ped_bird_owner_ring_idx",
            ),
            models.Index(fields=["life_status"], name="ped_bird_life_idx"),
            models.Index(fields=["ownership_status"], name="ped_bird_own_idx"),
            models.Index(
                fields=["activity_status"], name="ped_bird_activity_idx"
            ),
            models.Index(fields=["job"], name="ped_bird_job_idx"),
            models.Index(fields=["father"], name="ped_bird_father_idx"),
            models.Index(fields=["mother"], name="ped_bird_mother_idx"),
            models.Index(fields=["created"], name="ped_bird_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "normalized_ring_number"],
                condition=Q(deleted__isnull=True),
                name="uniq_live_pedigree_bird_ring_per_owner",
            ),
        ]

    def save(self, *args, **kwargs):
        self.normalized_ring_number = normalize_ring_number(self.ring_number)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"PedigreeBird #{self.pk} {self.ring_number}"


class PedigreeTemplate(BaseModel):
    name = models.CharField(max_length=255)
    external_pdf_id = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    pedigree_type = models.CharField(max_length=50, blank=True)
    report_name = models.CharField(max_length=255, blank=True)
    generation = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(PEDIGREE_MAX_GENERATION_DEPTH),
        ],
    )
    preview_image = models.CharField(max_length=2048, blank=True)
    is_active = models.BooleanField(default=True)
    is_premium = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(
                fields=["is_active", "sort_order"], name="ped_tpl_active_idx"
            ),
            models.Index(fields=["external_pdf_id"], name="ped_tpl_pdf_idx"),
        ]

    @property
    def is_configured(self):
        return bool(
            self.is_active
            and self.external_pdf_id
            and self.pedigree_type
            and self.report_name
            and self.generation
        )

    def __str__(self):
        return self.name


class PedigreeCertificateStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PROCESSING = "processing", _("Processing")
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")


class PedigreeCertificate(OwnerBaseModel):
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pedigree_certificates",
    )
    subject_bird = models.ForeignKey(
        PedigreeBird,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificates",
    )
    template = models.ForeignKey(
        PedigreeTemplate,
        on_delete=models.PROTECT,
        related_name="certificates",
    )
    status = models.CharField(
        max_length=20,
        choices=PedigreeCertificateStatus.choices,
        default=PedigreeCertificateStatus.PENDING,
    )
    generation_depth = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(PEDIGREE_MAX_GENERATION_DEPTH),
        ],
    )
    owner_data = models.JSONField(default=dict, blank=True)
    customization = models.JSONField(default=dict, blank=True)
    external_request_payload = models.JSONField(default=dict, blank=True)
    generated_file = models.FileField(
        upload_to="pedigree_certificates/",
        null=True,
        blank=True,
    )
    generated_filename = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    external_status_code = models.PositiveIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    generation_revision = models.PositiveIntegerField(default=0)
    generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["owner"], name="ped_cert_owner_idx"),
            models.Index(fields=["subject_bird"], name="ped_cert_subject_idx"),
            models.Index(fields=["template"], name="ped_cert_template_idx"),
            models.Index(fields=["status"], name="ped_cert_status_idx"),
            models.Index(fields=["created"], name="ped_cert_created_idx"),
            models.Index(
                fields=["owner", "status"], name="ped_cert_owner_status_idx"
            ),
        ]

    def __str__(self):
        return f"PedigreeCertificate #{self.pk} ({self.status})"


class PedigreeDocumentStatus(models.TextChoices):
    UPLOADED = "uploaded", _("Uploaded")
    PROCESSED = "processed", _("Processed")
    REVIEWED = "reviewed", _("Reviewed")
    FAILED = "failed", _("Failed")


class PedigreeDocument(OwnerBaseModel):
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pedigree_documents",
    )
    bird = models.ForeignKey(
        "assets.Bird",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pedigree_documents",
    )
    original_file = models.FileField(upload_to="pedigree_documents/")
    status = models.CharField(
        max_length=20,
        choices=PedigreeDocumentStatus.choices,
        default=PedigreeDocumentStatus.UPLOADED,
    )
    raw_ocr_text = models.TextField(blank=True)
    extracted_bird_ring_number = models.CharField(max_length=100, blank=True)
    extracted_description = models.TextField(blank=True)
    reviewed_bird_ring_number = models.CharField(max_length=100, blank=True)
    reviewed_description = models.TextField(blank=True)
    processing_error = models.TextField(blank=True)
    extracted_boxes = models.JSONField(default=list, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["owner"], name="pedigree_doc_owner_idx"),
            models.Index(
                fields=["uploaded_by"],
                name="pedigree_doc_user_idx",
            ),
            models.Index(fields=["bird"], name="pedigree_doc_bird_idx"),
            models.Index(fields=["status"], name="pedigree_doc_status_idx"),
            models.Index(fields=["created"], name="pedigree_doc_created_idx"),
        ]

    def __str__(self):
        return f"PedigreeDocument #{self.pk} ({self.status})"

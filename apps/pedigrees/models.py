import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_extensions.db.models import TimeStampedModel

from apps.general.models import BaseModel, OwnerBaseModel


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
    UNIDENTIFIED = "unidentified", _("Unidentified")


class PedigreeBirdOwnershipStatus(models.TextChoices):
    OWNED = "owned", _("Owned")
    SOLD = "sold", _("Sold")
    GIFTED = "gifted", _("Gifted")
    UNKNOWN = "unknown", _("Unknown")


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
    """A pedigree record.

    `registered_by` is the pedigree authority: it alone decides visibility,
    ring reuse/uniqueness, and which birds may be linked as father/mother.
    `owner` is physical/business ownership only and may be unknown (NULL);
    parentage never implies ownership.
    """

    owner = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_pedigreebirds",
        db_index=True,
    )
    registered_by = models.ForeignKey(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="registered_pedigree_birds",
    )
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
    # Biological/plumage colour of the bird.
    colour = models.CharField(
        max_length=40,
        choices=PedigreeBirdColour.choices,
        default=PedigreeBirdColour.UNKNOWN,
    )
    # Report styling, unrelated to `colour`: the last explicit `#RRGGBB`
    # colour of a successful pedigree generation that printed this bird. NULL
    # means its report box gets no colour unless the request sends one.
    default_color = models.CharField(max_length=7, null=True, blank=True)
    life_status = models.CharField(
        max_length=20,
        choices=PedigreeBirdLifeStatus.choices,
        default=PedigreeBirdLifeStatus.UNIDENTIFIED,
    )
    ownership_status = models.CharField(
        max_length=20,
        choices=PedigreeBirdOwnershipStatus.choices,
        default=PedigreeBirdOwnershipStatus.UNKNOWN,
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
                fields=["registered_by", "normalized_ring_number"],
                name="ped_bird_reg_ring_idx",
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
                fields=["registered_by", "normalized_ring_number"],
                condition=Q(deleted__isnull=True),
                name="uniq_live_pedigree_bird_ring_per_registrant",
            ),
        ]

    def save(self, *args, **kwargs):
        self.normalized_ring_number = normalize_ring_number(self.ring_number)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"PedigreeBird #{self.pk} {self.ring_number}"


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


class RegisteredPedigree(BaseModel):
    """A FastReport pedigree (`pdf_id` / `shape`) that PPW users may use.

    Public pedigrees are visible to every pedigree user; private ones only to
    UserAccounts with an explicit assignment row.
    """

    pedigree_id = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text=_("FastReport pdf_id / shape."),
    )
    is_public = models.BooleanField(default=True)
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="RegisteredPedigreeUser",
        related_name="registered_pedigrees",
        blank=True,
    )

    class Meta:
        ordering = ["pedigree_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["pedigree_id"],
                condition=Q(deleted__isnull=True),
                name="uniq_live_registered_pedigree_id",
            ),
        ]

    def __str__(self):
        return f"RegisteredPedigree {self.pedigree_id}"


class RegisteredPedigreeUser(TimeStampedModel):
    """Grants one UserAccount visibility of one private registered pedigree."""

    pedigree = models.ForeignKey(
        RegisteredPedigree,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="registered_pedigree_assignments",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["pedigree", "user"],
                name="uniq_registered_pedigree_user",
            ),
        ]

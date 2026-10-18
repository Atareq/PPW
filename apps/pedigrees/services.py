from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import json
import logging
from numbers import Number
from time import perf_counter
from urllib.parse import urljoin, urlparse
from zipfile import BadZipFile

from PIL import Image, ImageEnhance, ImageOps
import cv2
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import numpy as np
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
import pytesseract
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.assets.models import Bird
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeBirdLifeStatus,
    PedigreeBirdSex,
    PedigreeDocument,
    PedigreeDocumentStatus,
    RegisteredPedigree,
    normalize_ring_number,
)
from apps.users.constants import ProfileType
from apps.users.models import Profile

logger = logging.getLogger(__name__)


class PedigreeExtractionError(ValueError):
    pass


class PedigreeReportConfigurationError(APIException):
    status_code = 500
    default_code = "pedigree_report_not_configured"


def validate_pedigree_owner_profile(owner):
    if owner.type not in {ProfileType.CUSTOMER, ProfileType.SELLER}:
        raise ValidationError(
            {
                "owner": _(
                    "Only active customer or seller profiles can own pedigree " "birds."
                )
            }
        )


@dataclass
class PedigreeReviewResult:
    document: PedigreeDocument
    subject_bird_id: int
    path_to_bird_id: dict
    created_count: int
    reused_count: int
    updated_count: int


class PedigreeDocumentService:
    @staticmethod
    def upload_and_process(
        *,
        uploaded_by,
        owner,
        original_file,
        bird_id=None,
        box_detection_service=None,
    ):
        bird = PedigreeDocumentService._get_linked_bird(bird_id, owner)

        document = PedigreeDocument.objects.create(
            uploaded_by=uploaded_by,
            owner=owner,
            bird=bird,
            original_file=original_file,
            status=PedigreeDocumentStatus.UPLOADED,
        )

        box_detection_service = (
            box_detection_service or PedigreeImageBoxDetectionService()
        )

        try:
            boxes = box_detection_service.detect_boxes(document.original_file)
        except Exception as exc:
            return PedigreeDocumentService._mark_failed(
                document,
                f"Pedigree box detection failed: {exc}",
            )

        if not boxes:
            return PedigreeDocumentService._mark_failed(
                document,
                "No pedigree boxes were detected.",
            )

        boxes_with_lines = PedigreeBoxOCRService().extract_lines_for_boxes(
            document.original_file, boxes
        )
        document.extracted_boxes = boxes_with_lines
        document.status = PedigreeDocumentStatus.PROCESSED
        document.processing_error = ""
        document.save(
            update_fields=[
                "extracted_boxes",
                "status",
                "processing_error",
                "modified",
            ]
        )
        return PedigreeDocumentService.get_document(document.pk)

    @staticmethod
    @transaction.atomic
    def review_document(
        *,
        document_id,
        actor_profile,
        birds,
    ):
        return PedigreeReviewedBirdService.save_reviewed_pedigree(
            document_id=document_id,
            actor_profile=actor_profile,
            reviewed_nodes=birds,
        )

    @staticmethod
    def mark_document_reviewed_from_subject(*, document, subject_bird):
        document.reviewed_bird_ring_number = subject_bird.ring_number
        document.reviewed_description = subject_bird.description
        document.status = PedigreeDocumentStatus.REVIEWED
        document.reviewed_at = timezone.now()
        document.save(
            update_fields=[
                "reviewed_bird_ring_number",
                "reviewed_description",
                "status",
                "reviewed_at",
                "modified",
            ]
        )
        return document

    @staticmethod
    def lock_document_for_actor(document_id, actor_profile):
        return PedigreeDocumentService._get_locked_document_for_actor(
            document_id,
            actor_profile,
        )

    @staticmethod
    def get_document(document_id):
        return pedigree_document_queryset().get(pk=document_id)

    @staticmethod
    def _mark_failed(document, message, raw_ocr_text=None):
        if raw_ocr_text is not None:
            document.raw_ocr_text = raw_ocr_text
        document.status = PedigreeDocumentStatus.FAILED
        document.processing_error = message
        update_fields = ["status", "processing_error", "modified"]
        if raw_ocr_text is not None:
            update_fields.append("raw_ocr_text")
        document.save(update_fields=update_fields)
        return PedigreeDocumentService.get_document(document.pk)

    @staticmethod
    def _get_linked_bird(bird_id, owner):
        if bird_id is None:
            return None

        queryset = Bird.objects.all()
        if owner.type != ProfileType.MANAGER:
            queryset = queryset.filter(owner_id=owner.pk)

        try:
            return queryset.get(pk=bird_id)
        except Bird.DoesNotExist as exc:
            raise NotFound(_("Bird was not found.")) from exc

    @staticmethod
    def _get_locked_document_for_actor(document_id, actor_profile):
        queryset = PedigreeDocument.objects.select_for_update()
        if actor_profile.type != ProfileType.MANAGER:
            queryset = queryset.filter(owner_id=actor_profile.pk)

        try:
            return queryset.get(pk=document_id)
        except PedigreeDocument.DoesNotExist as exc:
            raise NotFound(_("Pedigree document was not found.")) from exc


class PedigreeBoxOCRService:
    def extract_lines_for_boxes(self, image_file, boxes):
        image = self._load_image(image_file)

        boxes_with_lines = []

        for box in boxes:
            crop = self._crop_box(image, box["bbox"])
            crop = self._preprocess_crop(crop)

            try:
                text = pytesseract.image_to_string(crop)
                lines = self._text_to_lines(text)
                box["lines"] = lines
                box["ocr_error"] = ""
            except Exception as exc:
                box["lines"] = []
                box["ocr_error"] = str(exc)

            boxes_with_lines.append(box)

        return boxes_with_lines

    def _load_image(self, image_file):
        if hasattr(image_file, "open"):
            image_file.open("rb")

        return Image.open(image_file).convert("RGB")

    def _crop_box(self, image, bbox):
        image_width, image_height = image.size

        x0 = int(bbox["x0"] * image_width)
        y0 = int(bbox["y0"] * image_height)
        x1 = int(bbox["x1"] * image_width)
        y1 = int(bbox["y1"] * image_height)

        padding = 6

        x0 = max(x0 - padding, 0)
        y0 = max(y0 - padding, 0)
        x1 = min(x1 + padding, image_width)
        y1 = min(y1 + padding, image_height)

        return image.crop((x0, y0, x1, y1))

    def _preprocess_crop(self, crop):
        crop = crop.convert("L")

        width, height = crop.size
        crop = crop.resize((width * 2, height * 2))

        crop = ImageOps.autocontrast(crop)

        enhancer = ImageEnhance.Contrast(crop)
        crop = enhancer.enhance(1.5)

        return crop

    def _text_to_lines(self, text):
        return [line.strip() for line in text.splitlines() if line.strip()]


def pedigree_document_queryset():
    return PedigreeDocument.objects.select_related(
        "uploaded_by",
        "owner",
        "bird",
    ).order_by("-created")


class RegisteredPedigreeService:
    """PPW authorization source of truth for FastReport pedigree ids."""

    @staticmethod
    def is_unrestricted(user):
        """Managers and superusers are not limited by user visibility."""
        return user.is_superuser or user.active_profile.type == ProfileType.MANAGER

    @classmethod
    def visible_queryset(cls, user):
        """Registered pedigrees visible to the user's account.

        Visibility is account-level, so switching Customer/Seller mode never
        changes it. Unauthorized private rows never enter the queryset.
        """
        queryset = RegisteredPedigree.objects.all()
        if cls.is_unrestricted(user):
            return queryset
        return queryset.filter(Q(is_public=True) | Q(assignments__user=user)).distinct()

    @classmethod
    def require_visible(cls, user, pedigree_id):
        """Raise 404 unless a non-manager user may use this pedigree id.

        Must run before any provider call. Unregistered and private
        unassigned ids both return 404 so their existence is not leaked.
        """
        if cls.is_unrestricted(user):
            return
        visible = cls.visible_queryset(user).filter(pedigree_id=pedigree_id)
        if not visible.exists():
            raise NotFound(_("Pedigree was not found."))


class PedigreeBirdValidator:
    """Database-aware validation for private pedigree birds and parents."""

    ALLOWED_OWNER_TYPES = {ProfileType.CUSTOMER, ProfileType.SELLER}

    @classmethod
    def validate(cls, *, attrs, owner, instance=None):
        if instance is None and owner.type not in cls.ALLOWED_OWNER_TYPES:
            raise ValidationError(
                {
                    "owner": _(
                        "Only active customer or seller profiles can create birds."
                    )
                }
            )

        merged = cls._merged_attrs(attrs, instance)
        cls._validate_dates(merged)
        cls._validate_unique_ring(merged, owner=owner, instance=instance)
        cls._validate_parents(merged, owner=owner, instance=instance)
        return attrs

    @staticmethod
    def _merged_attrs(attrs, instance):
        if instance is None:
            return dict(attrs)

        merged = {
            "ring_number": instance.ring_number,
            "birthday": instance.birthday,
            "record_date": instance.record_date,
            "father": instance.father,
            "mother": instance.mother,
        }
        merged.update(attrs)
        return merged

    @staticmethod
    def _validate_dates(data):
        today = timezone.localdate()
        birthday = data.get("birthday")
        record_date = data.get("record_date")
        if birthday and birthday > today:
            raise ValidationError({"birthday": _("Birthday cannot be in the future.")})
        if record_date and record_date > today:
            raise ValidationError(
                {"record_date": _("Record date cannot be in the future.")}
            )

    @staticmethod
    def _validate_unique_ring(data, *, owner, instance=None):
        normalized = normalize_ring_number(data.get("ring_number"))
        queryset = PedigreeBird.objects.filter(
            owner_id=owner.pk,
            normalized_ring_number=normalized,
            deleted__isnull=True,
        )
        if instance is not None:
            queryset = queryset.exclude(pk=instance.pk)
        if queryset.exists():
            raise ValidationError(
                {"ring_number": _("A live pedigree bird with this ring number exists.")}
            )

    @classmethod
    def _validate_parents(cls, data, *, owner, instance=None):
        father = data.get("father")
        mother = data.get("mother")

        if father and instance and father.pk == instance.pk:
            raise ValidationError({"father": _("A bird cannot be its own father.")})
        if mother and instance and mother.pk == instance.pk:
            raise ValidationError({"mother": _("A bird cannot be its own mother.")})
        if father and mother and father.pk == mother.pk:
            raise ValidationError(
                {"mother": _("Father and mother cannot be the same bird.")}
            )

        for field, parent in (("father", father), ("mother", mother)):
            if parent is None:
                continue
            if parent.owner_id != owner.pk:
                raise ValidationError({field: _("Parent bird was not found.")})
            if parent.deleted is not None:
                raise ValidationError(
                    {field: _("Deleted parent birds cannot be used.")}
                )

        if (
            father
            and father.sex != PedigreeBirdSex.UNKNOWN
            and father.sex != PedigreeBirdSex.MALE
        ):
            raise ValidationError(
                {"father": _("Father must be male when sex is known.")}
            )
        if (
            mother
            and mother.sex != PedigreeBirdSex.UNKNOWN
            and mother.sex != PedigreeBirdSex.FEMALE
        ):
            raise ValidationError(
                {"mother": _("Mother must be female when sex is known.")}
            )

        child_birthday = data.get("birthday")
        if child_birthday:
            for field, parent in (("father", father), ("mother", mother)):
                if parent and parent.birthday and parent.birthday >= child_birthday:
                    raise ValidationError(
                        {field: _("Parent birthday must be before child birthday.")}
                    )

        if instance is not None:
            for field, parent in (("father", father), ("mother", mother)):
                if parent and cls._parent_chain_reaches(
                    parent,
                    instance.pk,
                    proposed_father=father,
                    proposed_mother=mother,
                    instance_id=instance.pk,
                ):
                    raise ValidationError(
                        {field: _("Parent relationship would create a cycle.")}
                    )

    @classmethod
    def _parent_chain_reaches(
        cls,
        bird,
        target_id,
        *,
        proposed_father,
        proposed_mother,
        instance_id,
        seen=None,
    ):
        seen = seen or set()
        if bird.pk in seen:
            return False
        if bird.pk == target_id:
            return True
        seen.add(bird.pk)

        father = proposed_father if bird.pk == instance_id else bird.father
        mother = proposed_mother if bird.pk == instance_id else bird.mother
        return any(
            parent
            and cls._parent_chain_reaches(
                parent,
                target_id,
                proposed_father=proposed_father,
                proposed_mother=proposed_mother,
                instance_id=instance_id,
                seen=seen,
            )
            for parent in (father, mother)
        )


@dataclass
class PedigreeSpreadsheetImportResult:
    rows_processed: int
    birds_created: int
    birds_reused: int
    relationships_created: int
    relationships_unchanged: int
    ignored_legacy_values: int


@dataclass
class _PedigreeSpreadsheetCandidate:
    ring_number: str
    sex: str | None = None
    description: str | None = None
    father_ring: str | None = None
    mother_ring: str | None = None


class PedigreeSpreadsheetImportService:
    """Adapt the verified legacy XLSX layout into the PedigreeBird graph."""

    ROOT_RING_HEADER = "Estok_No"
    ROOT_SEX_HEADER = "Estok_Type"
    ROOT_DESCRIPTION_HEADER = "Estok_Notes"
    NO_DATA_SENTINELS = {"0", "102", "ffffffff"}
    COUNTRY_ONLY_RING_SENTINELS = {"dv", "nl", "nld", "be", "bel", "eg", "egy"}
    SEX_BY_LEGACY_VALUE = {
        "ذكر": PedigreeBirdSex.MALE,
        "أنثى": PedigreeBirdSex.FEMALE,
        "مجهول": PedigreeBirdSex.UNKNOWN,
    }

    @classmethod
    def import_workbook(cls, *, workbook_file, user):
        candidates, rows_processed, ignored_legacy_values = cls._parse_workbook(
            workbook_file
        )

        with transaction.atomic():
            owner = cls._resolve_owner(user)
            birds_by_ring = {
                bird.normalized_ring_number: bird
                for bird in PedigreeBird.objects.select_for_update().filter(
                    owner_id=owner.pk,
                    normalized_ring_number__in=candidates,
                    deleted__isnull=True,
                )
            }
            created_count = 0
            reused_count = 0

            for normalized_ring, candidate in candidates.items():
                bird = birds_by_ring.get(normalized_ring)
                if bird is None:
                    # Only new birds get a status; reused birds keep theirs.
                    attrs = {
                        "ring_number": candidate.ring_number,
                        "life_status": PedigreeBirdLifeStatus.UNIDENTIFIED,
                    }
                    if candidate.sex is not None:
                        attrs["sex"] = candidate.sex
                    if candidate.description is not None:
                        attrs["description"] = candidate.description
                    PedigreeBirdValidator.validate(
                        attrs=attrs,
                        owner=owner,
                    )
                    birds_by_ring[normalized_ring] = PedigreeBird.objects.create(
                        owner=owner,
                        **attrs,
                    )
                    created_count += 1
                    continue

                reused_count += 1
                cls._enrich_existing_bird(
                    bird=bird,
                    candidate=candidate,
                    owner=owner,
                )

            relationships_created, relationships_unchanged = cls._connect_relationships(
                candidates=candidates,
                birds_by_ring=birds_by_ring,
                owner=owner,
            )

        return PedigreeSpreadsheetImportResult(
            rows_processed=rows_processed,
            birds_created=created_count,
            birds_reused=reused_count,
            relationships_created=relationships_created,
            relationships_unchanged=relationships_unchanged,
            ignored_legacy_values=ignored_legacy_values,
        )

    @classmethod
    def _parse_workbook(cls, workbook_file):
        try:
            workbook_file.seek(0)
            workbook = load_workbook(
                workbook_file,
                read_only=True,
                data_only=True,
            )
        except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
            raise ValidationError(
                {"file": _("A valid XLSX file is required.")}
            ) from exc

        try:
            worksheet = workbook.worksheets[0]
            rows = worksheet.iter_rows(values_only=True)
            try:
                header_row = next(rows)
            except StopIteration as exc:
                raise ValidationError(
                    {"file": _("The XLSX file must include a header row.")}
                ) from exc

            headers = [cls._header_value(value) for value in header_row]
            cls._validate_headers(headers)
            candidates = {}
            ignored_legacy_values = 0
            rows_processed = 0

            for row_number, values in enumerate(rows, start=2):
                rows_processed += 1
                row = dict(zip(headers, values))
                ignored_legacy_values += cls._count_ignored_values(row)
                ignored_legacy_values += cls._collect_row(
                    row=row,
                    row_number=row_number,
                    candidates=candidates,
                )
        finally:
            workbook.close()

        return candidates, rows_processed, ignored_legacy_values

    @classmethod
    def _validate_headers(cls, headers):
        required_headers = cls._required_headers()
        missing = sorted(required_headers - set(headers))
        if missing:
            raise ValidationError(
                {
                    "file": _(
                        "The XLSX file is missing required pedigree headers: %(headers)s"
                    )
                    % {"headers": ", ".join(missing)}
                }
            )

    @classmethod
    def _collect_row(cls, *, row, row_number, candidates):
        ignored_sentinels = 0
        slots = {
            1: {
                "ring": cls.clean_legacy_ring(row[cls.ROOT_RING_HEADER]),
                "sex": cls._legacy_sex(
                    row[cls.ROOT_SEX_HEADER],
                    row_number=row_number,
                ),
                "description": cls._clean_legacy_text(
                    row.get(cls.ROOT_DESCRIPTION_HEADER)
                ),
            }
        }
        ignored_sentinels += cls._is_legacy_ring_sentinel(row[cls.ROOT_RING_HEADER])

        for index in range(1, 8):
            for relation, node in (("Father", index * 2), ("Mother", index * 2 + 1)):
                ring_header = f"Estok_{relation}{index}No"
                slots[node] = {
                    "ring": cls.clean_legacy_ring(row[ring_header]),
                    "description": cls._clean_legacy_text(
                        row.get(f"Estok_{relation}{index}")
                    ),
                }
                ignored_sentinels += cls._is_legacy_ring_sentinel(row[ring_header])

        for index in range(1, 9):
            target_node = 7 + index
            for relation, node in (
                ("Father", target_node * 2),
                ("Mother", target_node * 2 + 1),
            ):
                prefix = f"Estok_Father_{relation}{index}"
                ring_header = f"{prefix}_N"
                slots[node] = {
                    "ring": cls.clean_legacy_ring(row[ring_header]),
                    "description": cls._clean_legacy_text(row.get(f"{prefix}_D")),
                }
                ignored_sentinels += cls._is_legacy_ring_sentinel(row[ring_header])

        if slots[1]["ring"] is None:
            raise ValidationError(
                {
                    "file": _("Row %(row)s requires a valid Estok_No ring number.")
                    % {"row": row_number}
                }
            )

        cls._collect_reachable_node(
            node=1,
            slots=slots,
            candidates=candidates,
        )
        return ignored_sentinels

    @classmethod
    def _collect_reachable_node(cls, *, node, slots, candidates):
        node_data = slots.get(node)
        if node_data is None or node_data["ring"] is None:
            return

        candidate = cls._candidate_for(
            candidates=candidates,
            ring_number=node_data["ring"],
        )
        cls._merge_candidate_scalar(
            candidate=candidate,
            field="sex",
            value=node_data.get("sex"),
        )
        cls._merge_candidate_description(
            candidate=candidate,
            value=node_data.get("description"),
            prefer=node == 1,
        )

        for relation, child_node in (("father", node * 2), ("mother", node * 2 + 1)):
            child = slots.get(child_node)
            if child is None or child["ring"] is None:
                continue
            cls._merge_candidate_scalar(
                candidate=candidate,
                field=f"{relation}_ring",
                value=child["ring"],
            )
            cls._collect_reachable_node(
                node=child_node,
                slots=slots,
                candidates=candidates,
            )

    @classmethod
    def _candidate_for(cls, *, candidates, ring_number):
        candidate = candidates.get(ring_number)
        if candidate is None:
            candidate = _PedigreeSpreadsheetCandidate(ring_number=ring_number)
            candidates[ring_number] = candidate
        return candidate

    @staticmethod
    def _merge_candidate_scalar(*, candidate, field, value):
        if value is None:
            return
        current = getattr(candidate, field)
        if current is None:
            setattr(candidate, field, value)
            return
        if current != value:
            raise ValidationError(
                {
                    "file": _(
                        "Conflicting %(field)s values were supplied for ring %(ring)s."
                    )
                    % {"field": field, "ring": candidate.ring_number}
                }
            )

    @staticmethod
    def _merge_candidate_description(*, candidate, value, prefer=False):
        if value is None:
            return
        if candidate.description is None or prefer:
            candidate.description = value

    @classmethod
    def _enrich_existing_bird(cls, *, bird, candidate, owner):
        changed_fields = []
        if candidate.sex is not None and candidate.sex != bird.sex:
            if bird.sex != PedigreeBirdSex.UNKNOWN:
                cls._raise_existing_conflict(
                    field="sex",
                    ring_number=bird.ring_number,
                )
            bird.sex = candidate.sex
            changed_fields.append("sex")
        if candidate.description is not None and not bird.description:
            bird.description = candidate.description
            changed_fields.append("description")
        if changed_fields:
            PedigreeBirdValidator.validate(
                attrs={field: getattr(bird, field) for field in changed_fields},
                owner=owner,
                instance=bird,
            )
            bird.save(update_fields=[*changed_fields, "modified"])

    @classmethod
    def _connect_relationships(cls, *, candidates, birds_by_ring, owner):
        created_count = 0
        unchanged_count = 0
        for normalized_ring, candidate in candidates.items():
            bird = birds_by_ring[normalized_ring]
            relation_attrs = {}
            for relation in ("father", "mother"):
                parent_ring = getattr(candidate, f"{relation}_ring")
                if parent_ring is None:
                    continue
                parent = birds_by_ring[parent_ring]
                current_parent_id = getattr(bird, f"{relation}_id")
                if current_parent_id == parent.pk:
                    unchanged_count += 1
                    continue
                if current_parent_id is not None:
                    cls._raise_existing_conflict(
                        field=relation,
                        ring_number=bird.ring_number,
                    )
                relation_attrs[relation] = parent

            if not relation_attrs:
                continue
            PedigreeBirdValidator.validate(
                attrs=relation_attrs,
                owner=owner,
                instance=bird,
            )
            for relation, parent in relation_attrs.items():
                setattr(bird, relation, parent)
            bird.save(update_fields=[*relation_attrs, "modified"])
            created_count += len(relation_attrs)
        return created_count, unchanged_count

    @staticmethod
    def _raise_existing_conflict(*, field, ring_number):
        raise ValidationError(
            {
                "file": _("Existing %(field)s data conflicts for ring %(ring)s.")
                % {"field": field, "ring": ring_number}
            }
        )

    @classmethod
    def _resolve_owner(cls, user):
        profiles = list(
            Profile.objects.select_for_update()
            .filter(
                user_id=user.pk,
                type__in=[ProfileType.CUSTOMER, ProfileType.SELLER],
                activated=True,
                is_valid=True,
                deleted__isnull=True,
            )
            .order_by("pk")
        )
        if len(profiles) != 1:
            raise ValidationError(
                {
                    "user": _(
                        "The selected user must have exactly one eligible customer or seller profile."
                    )
                }
            )
        return profiles[0]

    @classmethod
    def _legacy_sex(cls, value, *, row_number):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return cls.SEX_BY_LEGACY_VALUE[text]
        except KeyError as exc:
            raise ValidationError(
                {
                    "file": _("Row %(row)s has an unsupported Estok_Type value.")
                    % {"row": row_number}
                }
            ) from exc

    @classmethod
    def _required_headers(cls):
        headers = {cls.ROOT_RING_HEADER, cls.ROOT_SEX_HEADER}
        for index in range(1, 8):
            headers.update(
                {
                    f"Estok_Father{index}No",
                    f"Estok_Mother{index}No",
                }
            )
        for index in range(1, 9):
            headers.update(
                {
                    f"Estok_Father_Father{index}_N",
                    f"Estok_Father_Mother{index}_N",
                }
            )
        return headers

    @classmethod
    def _used_headers(cls):
        headers = set(cls._required_headers())
        headers.add(cls.ROOT_DESCRIPTION_HEADER)
        for index in range(1, 8):
            headers.update(
                {
                    f"Estok_Father{index}",
                    f"Estok_Mother{index}",
                }
            )
        for index in range(1, 9):
            headers.update(
                {
                    f"Estok_Father_Father{index}_D",
                    f"Estok_Father_Mother{index}_D",
                }
            )
        return headers

    @classmethod
    def _count_ignored_values(cls, row):
        return sum(
            1
            for header, value in row.items()
            if header not in cls._used_headers() and cls._has_value(value)
        )

    @classmethod
    def clean_legacy_ring(cls, value):
        if cls._is_legacy_ring_sentinel(value):
            return None
        return normalize_ring_number(str(value).strip())

    @classmethod
    def _clean_legacy_text(cls, value):
        if cls._is_legacy_sentinel(value):
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _is_legacy_sentinel(cls, value):
        if value is None:
            return True
        if isinstance(value, Number) and not isinstance(value, bool):
            if value in {0, 102}:
                return True
        text = str(value).strip()
        return not text or text.casefold() in cls.NO_DATA_SENTINELS

    @classmethod
    def _is_legacy_ring_sentinel(cls, value):
        if cls._is_legacy_sentinel(value):
            return True
        text = str(value).strip()
        folded = text.casefold()
        if set(folded) == {"0"} or folded in cls.COUNTRY_ONLY_RING_SENTINELS:
            return True
        return any("\u0600" <= ch <= "\u06FF" for ch in text) and not any(
            ch.isdigit() for ch in text
        )

    @staticmethod
    def _has_value(value):
        return value is not None and bool(str(value).strip())

    @staticmethod
    def _header_value(value):
        return str(value).strip() if value is not None else ""


class PedigreeReviewedBirdService:
    """Persist user-reviewed OCR nodes into the canonical PedigreeBird graph."""

    SCALAR_FIELDS = [
        "ring_number",
        "ring_code",
        "name",
        "description",
        "record_date",
        "birthday",
        "sex",
        "colour",
        "life_status",
        "ownership_status",
        "activity_status",
        "job",
        "external_owner_name",
    ]

    @classmethod
    @transaction.atomic
    def save_reviewed_pedigree(cls, *, document_id, actor_profile, reviewed_nodes):
        document = PedigreeDocumentService.lock_document_for_actor(
            document_id,
            actor_profile,
        )
        owner = document.owner
        validate_pedigree_owner_profile(owner)

        merged_by_ring, path_to_ring = cls._merge_nodes_by_normalized_ring(
            reviewed_nodes
        )
        birds_by_ring, created_ids, scalar_updated_ids = cls._create_or_update_birds(
            owner=owner,
            merged_by_ring=merged_by_ring,
        )
        path_to_bird = {
            path: birds_by_ring[normalized_ring]
            for path, normalized_ring in path_to_ring.items()
        }

        relationship_updated_ids = cls._connect_relationships(path_to_bird)

        subject_bird = path_to_bird[""]
        PedigreeDocumentService.mark_document_reviewed_from_subject(
            document=document,
            subject_bird=subject_bird,
        )
        document = PedigreeDocumentService.get_document(document.pk)
        existing_ids = {bird.pk for bird in birds_by_ring.values()} - created_ids
        updated_existing_ids = (
            scalar_updated_ids | relationship_updated_ids
        ) - created_ids
        reused_ids = existing_ids - updated_existing_ids

        return PedigreeReviewResult(
            document=document,
            subject_bird_id=subject_bird.pk,
            path_to_bird_id={
                path: bird.pk for path, bird in sorted(path_to_bird.items())
            },
            created_count=len(created_ids),
            reused_count=len(reused_ids),
            updated_count=len(updated_existing_ids),
        )

    @classmethod
    def _merge_nodes_by_normalized_ring(cls, reviewed_nodes):
        merged_by_ring = {}
        path_to_ring = {}

        for node in reviewed_nodes:
            normalized_ring = normalize_ring_number(node["ring_number"])
            if not normalized_ring:
                raise ValidationError({"ring_number": _("Ring number is required.")})
            path_to_ring[node["path"]] = normalized_ring

            if normalized_ring not in merged_by_ring:
                merged_by_ring[normalized_ring] = {
                    field: node[field] for field in cls.SCALAR_FIELDS if field in node
                }
                continue

            merged = merged_by_ring[normalized_ring]
            for field in cls.SCALAR_FIELDS:
                if field not in node:
                    continue
                if field == "ring_number":
                    continue
                if field in merged and merged[field] != node[field]:
                    raise ValidationError(
                        {
                            field: _(
                                "Repeated pedigree bird definitions contain "
                                "conflicting values."
                            )
                        }
                    )
                merged[field] = node[field]

        return merged_by_ring, path_to_ring

    @classmethod
    def _create_or_update_birds(cls, *, owner, merged_by_ring):
        existing_birds = {
            bird.normalized_ring_number: bird
            for bird in PedigreeBird.objects.select_for_update().filter(
                owner_id=owner.pk,
                normalized_ring_number__in=merged_by_ring.keys(),
                deleted__isnull=True,
            )
        }
        birds_by_ring = {}
        created_ids = set()
        updated_ids = set()

        for normalized_ring, attrs in merged_by_ring.items():
            bird = existing_birds.get(normalized_ring)
            if bird is None:
                PedigreeBirdValidator.validate(
                    attrs=attrs,
                    owner=owner,
                    instance=None,
                )
                bird = PedigreeBird.objects.create(owner=owner, **attrs)
                created_ids.add(bird.pk)
                birds_by_ring[normalized_ring] = bird
                continue

            changed_fields = []
            for field, value in attrs.items():
                if getattr(bird, field) != value:
                    setattr(bird, field, value)
                    changed_fields.append(field)

            if changed_fields:
                PedigreeBirdValidator.validate(
                    attrs={field: getattr(bird, field) for field in changed_fields},
                    owner=owner,
                    instance=bird,
                )
                update_fields = [*changed_fields, "modified"]
                if "ring_number" in changed_fields:
                    update_fields.append("normalized_ring_number")
                bird.save(update_fields=update_fields)
                updated_ids.add(bird.pk)

            birds_by_ring[normalized_ring] = bird

        return birds_by_ring, created_ids, updated_ids

    @classmethod
    def _connect_relationships(cls, path_to_bird):
        desired_by_child = {}
        for path, child in path_to_bird.items():
            for relation, edge in (("father", "F"), ("mother", "M")):
                parent = path_to_bird.get(f"{path}{edge}")
                if parent is None:
                    continue
                desired = desired_by_child.setdefault(child.pk, {"bird": child})
                existing_parent = desired.get(relation)
                if existing_parent is not None and existing_parent.pk != parent.pk:
                    raise ValidationError(
                        {
                            relation: _(
                                "Repeated pedigree paths assign conflicting parents "
                                "to the same bird."
                            )
                        }
                    )
                desired[relation] = parent

        updated_ids = set()
        for path in sorted(path_to_bird, key=len):
            child = path_to_bird[path]
            desired = desired_by_child.get(child.pk)
            if not desired:
                continue

            relation_attrs = {
                relation: desired[relation]
                for relation in ("father", "mother")
                if relation in desired
            }
            PedigreeBirdValidator.validate(
                attrs=relation_attrs,
                owner=child.owner,
                instance=child,
            )

            changed_fields = []
            for relation, parent in relation_attrs.items():
                if getattr(child, f"{relation}_id") != parent.pk:
                    setattr(child, relation, parent)
                    changed_fields.append(relation)
            if changed_fields:
                child.save(update_fields=[*changed_fields, "modified"])
                updated_ids.add(child.pk)

        return updated_ids


class PedigreeTreeService:
    NODE_FIELDS = [
        "ring_number",
        "ring_code",
        "name",
        "description",
        "record_date",
        "birthday",
        "sex",
        "colour",
        "life_status",
        "ownership_status",
        "activity_status",
        "job",
        "external_owner_name",
    ]

    @classmethod
    def build_generation_data(
        cls,
        *,
        subject_bird,
        generation_depth,
    ):
        # generation_depth is the total number of levels, with the subject at
        # depth 1. A depth of 2 includes only the subject and direct parents.
        return {
            "subject": cls._bird_node(
                subject_bird,
                max_depth=generation_depth,
            ),
        }

    @classmethod
    def build_path_lookup(cls, pedigree_data, *, max_generation):
        lookup = {"": pedigree_data.get("subject") or None}
        cls._collect_paths(
            pedigree_data.get("subject"),
            path="",
            depth=1,
            max_generation=max_generation,
            lookup=lookup,
        )
        return lookup

    @classmethod
    def _bird_node(cls, bird, *, max_depth, depth=1, seen=None):
        if bird is None:
            return None
        seen = seen or set()
        if bird.pk in seen:
            raise ValidationError({"subject": _("Pedigree tree contains a cycle.")})
        seen.add(bird.pk)

        node = {
            "ring_number": bird.ring_number,
            "ring_code": bird.ring_code,
            "name": bird.name,
            "description": bird.description,
            "record_date": cls._date_value(bird.record_date),
            "birthday": cls._date_value(bird.birthday),
            "sex": bird.sex,
            "colour": bird.colour,
            "life_status": bird.life_status,
            "ownership_status": bird.ownership_status,
            "activity_status": bird.activity_status,
            "job": bird.job,
            "external_owner_name": bird.external_owner_name,
        }
        if depth >= max_depth:
            node["father"] = None
            node["mother"] = None
            return node
        node["father"] = cls._bird_node(
            bird.father,
            max_depth=max_depth,
            depth=depth + 1,
            seen=set(seen),
        )
        node["mother"] = cls._bird_node(
            bird.mother,
            max_depth=max_depth,
            depth=depth + 1,
            seen=set(seen),
        )
        return node

    @staticmethod
    def _date_value(value):
        if value is None:
            return None
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @classmethod
    def _collect_paths(cls, node, *, path, depth, max_generation, lookup):
        if not node or depth >= max_generation:
            return
        for key, edge in (("father", "F"), ("mother", "M")):
            child_path = f"{path}{edge}"
            child = node.get(key)
            lookup[child_path] = child
            if child is not None:
                cls._collect_paths(
                    child,
                    path=child_path,
                    depth=depth + 1,
                    max_generation=max_generation,
                    lookup=lookup,
                )


ANCESTOR_FIELD_MAPPING = {
    "FF": ("Father1_NO", "Father1"),
    "FM": ("Mother1_NO", "Mother1"),
    "MF": ("Father2_1_NO", "Father2_1"),
    "MM": ("Mother2_1_NO", "Mother2_1"),
    "FFF": ("Father2_NO", "Father2"),
    "FFM": ("Mother2_NO", "Mother2"),
    "FMF": ("Father3_NO", "Father3"),
    "FMM": ("Mother3_NO", "Mother3"),
    "MFF": ("Father2_2_NO", "Father2_2"),
    "MFM": ("Mother2_2_NO", "Mother2_2"),
    "MMF": ("Father2_3_NO", "Father2_3"),
    "MMM": ("Mother2_3_NO", "Mother2_3"),
    "FFFF": ("Father4_NO", "Father4"),
    "FFFM": ("Mother4_NO", "Mother4"),
    "FFMF": ("Father5_NO", "Father5"),
    "FFMM": ("Mother5_NO", "Mother5"),
    "FMFF": ("Father6_NO", "Father6"),
    "FMFM": ("Mother6_NO", "Mother6"),
    "FMMF": ("Father7_NO", "Father7"),
    "FMMM": ("Mother7_NO", "Mother7"),
    "MFFF": ("Father2_4_NO", "Father2_4"),
    "MFFM": ("Mother2_4_NO", "Mother2_4"),
    "MFMF": ("Father2_5_NO", "Father2_5"),
    "MFMM": ("Mother2_5_NO", "Mother2_5"),
    "MMFF": ("Father2_6_NO", "Father2_6"),
    "MMFM": ("Mother2_6_NO", "Mother2_6"),
    "MMMF": ("Father2_7_NO", "Father2_7"),
    "MMMM": ("Mother2_7_NO", "Mother2_7"),
    "FFFFF": ("Father6_01_NO", "Father6_01"),
    "FFFFM": ("Mother6_01_NO", "Mother6_01"),
    "FFFMF": ("Father6_02_NO", "Father6_02"),
    "FFFMM": ("Mother6_02_NO", "Mother6_02"),
    "FFMFF": ("Father6_03_NO", "Father6_03"),
    "FFMFM": ("Mother6_03_NO", "Mother6_03"),
    "FFMMF": ("Father6_04_NO", "Father6_04"),
    "FFMMM": ("Mother6_04_NO", "Mother6_04"),
    "FMFFF": ("Father6_05_NO", "Father6_05"),
    "FMFFM": ("Mother6_05_NO", "Mother6_05"),
    "FMFMF": ("Father6_06_NO", "Father6_06"),
    "FMFMM": ("Mother6_06_NO", "Mother6_06"),
    "FMMFF": ("Father6_07_NO", "Father6_07"),
    "FMMFM": ("Mother6_07_NO", "Mother6_07"),
    "FMMMF": ("Father6_08_NO", "Father6_08"),
    "FMMMM": ("Mother6_08_NO", "Mother6_08"),
    "MFFFF": ("Father6_09_NO", "Father6_09"),
    "MFFFM": ("Mother6_09_NO", "Mother6_09"),
    "MFFMF": ("Father6_10_NO", "Father6_10"),
    "MFFMM": ("Mother6_10_NO", "Mother6_10"),
    "MFMFF": ("Father6_11_NO", "Father6_11"),
    "MFMFM": ("Mother6_11_NO", "Mother6_11"),
    "MFMMF": ("Father6_12_NO", "Father6_12"),
    "MFMMM": ("Mother6_12_NO", "Mother6_12"),
    "MMFFF": ("Father6_13_NO", "Father6_13"),
    "MMFFM": ("Mother6_13_NO", "Mother6_13"),
    "MMFMF": ("Father6_14_NO", "Father6_14"),
    "MMFMM": ("Mother6_14_NO", "Mother6_14"),
    "MMMFF": ("Father6_15_NO", "Father6_15"),
    "MMMFM": ("Mother6_15_NO", "Mother6_15"),
    "MMMMF": ("Father6_16_NO", "Father6_16"),
    "MMMMM": ("Mother6_16_NO", "Mother6_16"),
}


class PedigreeReportMapper:
    OWNER_PARAMETER_MAP = {
        "name": "Farth_Name",
        "loft_name": "OwnerLoftName",
        "phone": "OwnerPhone",
        "whatsapp": "OwnerWhatsApp",
        "email": "OwnerEmail",
    }

    CUSTOMIZATION_PARAMETER_MAP = {
        "background_image": "BackgroundImage",
        "bird_image": "Farkh_Image",
        "details_pic": "Details_Pic",
        "compuclub": "Compuclub",
        "bird_type": "BirdType",
    }

    @classmethod
    def merge_payload(
        cls,
        *,
        frontend_report,
        subject_bird,
        generation_depth,
        owner=None,
        customization=None,
    ):
        payload = deepcopy(frontend_report)

        # Prevent report.generation and generation_depth from becoming different.
        payload["generation"] = generation_depth

        parameters = payload.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
            payload["parameters"] = parameters

        pedigree_data = PedigreeTreeService.build_generation_data(
            subject_bird=subject_bird,
            generation_depth=generation_depth,
        )

        parameters.update(
            cls._bird_parameters(
                pedigree_data,
                generation_depth,
            )
        )
        parameters.update(cls._owner_parameters(owner or {}))
        parameters.update(cls._customization_parameters(customization or {}))

        parameters["PrintDate"] = timezone.localdate().isoformat()

        return payload

    @classmethod
    def _bird_parameters(cls, pedigree_data, generation_depth):
        lookup = PedigreeTreeService.build_path_lookup(
            pedigree_data,
            max_generation=generation_depth,
        )

        subject = lookup.get("")
        father = lookup.get("F")
        mother = lookup.get("M")

        parameters = {
            # Subject ring
            "Farkh_No": cls._ring(subject),
            "Farth_No": cls._ring(subject),
            # Subject
            "Esmah_Name": cls._name(subject),
            "Esmah_FullNo": cls._description(subject),
            # Direct father
            "Farth_FatherNo": cls._ring(father),
            "Father_Name": cls._name(father),
            "Father_Info": cls._description(father),
            # Direct mother
            "Farth_MotherNo": cls._ring(mother),
            "Mother_Name": cls._name(mother),
            "Mother_Info": cls._description(mother),
        }

        for path, (
            ring_field,
            description_field,
        ) in ANCESTOR_FIELD_MAPPING.items():
            node = lookup.get(path)

            parameters[ring_field] = cls._ring(node)
            parameters[f"{description_field}_Name"] = cls._name(node)
            parameters[description_field] = cls._description(node)

        return parameters

    @classmethod
    def _owner_parameters(cls, owner):
        parameters = {}

        for key, parameter_name in cls.OWNER_PARAMETER_MAP.items():
            if key not in owner:
                continue

            value = owner.get(key) or ""
            parameters[parameter_name] = value

            if key == "loft_name":
                # The provider currently supports both spellings.
                parameters["Owner_LoftName"] = value

        return parameters

    @classmethod
    def _customization_parameters(cls, customization):
        parameters = {}

        for key, parameter_name in cls.CUSTOMIZATION_PARAMETER_MAP.items():
            if key in customization:
                parameters[parameter_name] = customization.get(key) or ""

        return parameters

    @staticmethod
    def _ring(node):
        return (node or {}).get("ring_number", "") or ""

    @staticmethod
    def _name(node):
        return (node or {}).get("name", "") or ""

    @staticmethod
    def _description(node):
        return (node or {}).get("description", "") or ""


class PedigreeProviderSyntheticPayloadBuilder:
    TEST_SHAPE = 4
    TEST_GENERATION = 6
    TEST_FILE_NAME = "PPW_Provider_Test_S04_G6.pdf"
    DIAGNOSTIC_DESCRIPTION = (
        "PPW synthetic provider diagnostic record. "
        "Generation 6 health test. No real user data."
    )
    STYLE_SUFFIXES = [
        ("TextColor", "#FFFFFF"),
        ("BackColor", "#1D4ED8"),
        ("BoxBackColor", "#EFF6FF"),
        ("BorderColor", "#1E3A8A"),
        ("BoxShape", ""),
    ]

    @classmethod
    def build(cls):
        parameters = cls._bird_parameters()
        parameters.update(cls._owner_parameters())
        parameters.update(cls._customization_parameters())
        parameters.update(cls._style_parameters(parameters))
        return {
            "generation": cls.TEST_GENERATION,
            "fileName": cls.TEST_FILE_NAME,
            "parameters": parameters,
            "dataSources": [],
        }

    @classmethod
    def _bird_parameters(cls):
        parameters = {
            "Farkh_No": "PPW-TEST-ROOT",
            "Farth_No": "PPW-TEST-ROOT",
            "Esmah_Name": "PPW Synthetic Subject",
            "Esmah_FullNo": cls.DIAGNOSTIC_DESCRIPTION,
            "Farth_FatherNo": "PPW-TEST-F",
            "Father_Name": "PPW Synthetic Father",
            "Father_Info": cls.DIAGNOSTIC_DESCRIPTION,
            "Farth_MotherNo": "PPW-TEST-M",
            "Mother_Name": "PPW Synthetic Mother",
            "Mother_Info": cls.DIAGNOSTIC_DESCRIPTION,
        }

        for path, (
            ring_field,
            description_field,
        ) in ANCESTOR_FIELD_MAPPING.items():
            parameters[ring_field] = f"PPW-TEST-{path}"
            parameters[f"{description_field}_Name"] = f"PPW Synthetic Ancestor {path}"
            parameters[description_field] = cls.DIAGNOSTIC_DESCRIPTION

        return parameters

    @staticmethod
    def _owner_parameters():
        return {
            "Farth_Name": "PPW Diagnostic Owner",
            "Owner_LoftName": "PPW Diagnostic Loft",
            "OwnerLoftName": "PPW Diagnostic Loft",
            "OwnerPhone": "0000000000",
            "OwnerWhatsApp": "0000000000",
            "OwnerEmail": "diagnostic@example.invalid",
        }

    @staticmethod
    def _customization_parameters():
        return {
            "BackgroundImage": "",
            "Farkh_Image": "",
            "Details_Pic": "",
            "BirdType": "Diagnostic Pigeon",
            "Compuclub": "PPW Provider Diagnostic",
        }

    @classmethod
    def _style_parameters(cls, parameters):
        style_parameters = {}
        for parameter_name, value in parameters.items():
            if not isinstance(value, str):
                continue
            for suffix, style_value in cls.STYLE_SUFFIXES:
                style_parameters[f"{parameter_name}_{suffix}"] = style_value
        return style_parameters


class PedigreeProviderDiagnosticService:
    TEST_SHAPE = PedigreeProviderSyntheticPayloadBuilder.TEST_SHAPE
    TEST_GENERATION = PedigreeProviderSyntheticPayloadBuilder.TEST_GENERATION
    MIN_VALID_PDF_SIZE_BYTES = 1000

    @classmethod
    def run(cls):
        checked_at = timezone.now()
        total_started_at = perf_counter()
        client = PedigreeReportClient()

        health = cls._check_health(client)
        generation = cls._generate_pdf(client)

        total_response_time = perf_counter() - total_started_at
        overall_status = cls._overall_status(health, generation)
        result = {
            "status": overall_status,
            "checked_at": checked_at.isoformat(),
            "health": health,
            "pedigree_generation": generation,
            "total_response_time_seconds": round(total_response_time, 3),
            "feedback": cls._feedback(health, generation),
        }
        logger.info(
            "Pedigree provider diagnostic completed.",
            extra={
                "operation": "provider-test",
                "overall_status": overall_status,
                "health_status_code": health["status_code"],
                "health_duration_seconds": health["response_time_seconds"],
                "generation_status_code": generation["status_code"],
                "generation_duration_seconds": generation["response_time_seconds"],
                "pdf_size_bytes": generation["pdf_size_bytes"],
                "valid_pdf": generation["valid_pdf"],
                "shape": cls.TEST_SHAPE,
                "generation": cls.TEST_GENERATION,
            },
        )
        return result

    @classmethod
    def _check_health(cls, client):
        started_at = perf_counter()
        try:
            response = client.get_health()
        except PedigreeReportConfigurationError:
            return cls._health_failure(started_at, "configuration_error")
        except TimeoutError:
            return cls._health_failure(started_at, "timeout")
        except Exception as exc:
            import requests

            if isinstance(exc, requests.exceptions.Timeout):
                return cls._health_failure(started_at, "timeout")
            if isinstance(exc, requests.exceptions.ConnectionError):
                return cls._health_failure(started_at, "connection_error")
            logger.exception(
                "Unexpected pedigree provider health diagnostic error.",
                extra={"operation": "provider-test-health"},
            )
            return cls._health_failure(started_at, "unexpected_error")

        provider_status = cls._provider_health_status(response)
        ok = response.status_code == 200
        return {
            "ok": ok,
            "provider_status": provider_status,
            "status_code": response.status_code,
            "response_time_seconds": round(perf_counter() - started_at, 3),
            "error": None if ok else "provider_health_failed",
        }

    @classmethod
    def _generate_pdf(cls, client):
        started_at = perf_counter()
        try:
            response = client.post_pdf(
                pdf_id=cls.TEST_SHAPE,
                payload=PedigreeProviderSyntheticPayloadBuilder.build(),
                accept="application/pdf",
            )
        except PedigreeReportConfigurationError:
            return cls._generation_failure(started_at, "configuration_error")
        except Exception as exc:
            import requests

            if isinstance(exc, requests.exceptions.Timeout):
                return cls._generation_failure(started_at, "timeout")
            if isinstance(exc, requests.exceptions.ConnectionError):
                return cls._generation_failure(started_at, "connection_error")
            logger.exception(
                "Unexpected pedigree provider generation diagnostic error.",
                extra={"operation": "provider-test-generation"},
            )
            return cls._generation_failure(started_at, "unexpected_error")

        content_type = response.headers.get("Content-Type")
        pdf_size = len(response.content or b"")
        valid_pdf = cls.is_valid_pdf_response(response)
        ok = response.status_code == 200 and valid_pdf
        error = None
        if response.status_code != 200:
            error = "provider_generation_failed"
        elif not valid_pdf:
            error = "invalid_pdf"
        return {
            "ok": ok,
            "shape": cls.TEST_SHAPE,
            "generation": cls.TEST_GENERATION,
            "status_code": response.status_code,
            "content_type": content_type,
            "response_time_seconds": round(perf_counter() - started_at, 3),
            "pdf_size_bytes": pdf_size,
            "valid_pdf": valid_pdf,
            "error": error,
        }

    @classmethod
    def _health_failure(cls, started_at, error):
        return {
            "ok": False,
            "provider_status": None,
            "status_code": None,
            "response_time_seconds": round(perf_counter() - started_at, 3),
            "error": error,
        }

    @classmethod
    def _generation_failure(cls, started_at, error):
        return {
            "ok": False,
            "shape": cls.TEST_SHAPE,
            "generation": cls.TEST_GENERATION,
            "status_code": None,
            "content_type": None,
            "response_time_seconds": round(perf_counter() - started_at, 3),
            "pdf_size_bytes": 0,
            "valid_pdf": False,
            "error": error,
        }

    @classmethod
    def _overall_status(cls, health, generation):
        if (
            health["error"] == "configuration_error"
            or generation["error"] == "configuration_error"
        ):
            return "unhealthy"
        if health["ok"] and generation["ok"]:
            return "healthy"
        if health["ok"] or generation["ok"]:
            return "degraded"
        return "unhealthy"

    @staticmethod
    def _feedback(health, generation):
        if health["ok"]:
            service = "Pedigree server health check succeeded."
        elif health["error"] == "connection_error":
            service = "Pedigree server is currently unreachable."
        elif health["error"] == "timeout":
            service = "Pedigree server health check timed out."
        elif health["error"] == "configuration_error":
            service = "Pedigree provider configuration is incomplete."
        else:
            service = "Pedigree server health check is currently failing."

        if generation["ok"]:
            generation_feedback = (
                "Six-generation synthetic pedigree PDF generation succeeded."
            )
        elif generation["error"] == "connection_error":
            generation_feedback = (
                "Synthetic pedigree generation could not be performed."
            )
        elif generation["error"] == "timeout":
            generation_feedback = "Pedigree PDF generation timed out."
        elif generation["error"] == "invalid_pdf":
            generation_feedback = "Pedigree PDF generation returned an invalid PDF."
        elif generation["error"] == "configuration_error":
            generation_feedback = "Pedigree PDF generation configuration is incomplete."
        else:
            generation_feedback = "Pedigree PDF generation is currently failing."

        if health["ok"] and generation["ok"]:
            service = "Pedigree server is reachable and healthy."

        return {
            "service": service,
            "generation": generation_feedback,
        }

    @classmethod
    def is_valid_pdf_response(cls, response):
        content_type = response.headers.get("Content-Type", "")
        content = response.content or b""
        return (
            response.status_code == 200
            and "application/pdf" in content_type.lower()
            and content.startswith(b"%PDF")
            and len(content) > cls.MIN_VALID_PDF_SIZE_BYTES
        )

    @staticmethod
    def _provider_health_status(response):
        content = response.content or b""
        if not content:
            return None
        try:
            body = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return content.decode("utf-8", errors="replace").strip() or None
        if isinstance(body, dict):
            for key in ("status", "Status", "health", "Health"):
                if key in body:
                    return body[key]
        if isinstance(body, str):
            return body
        return None


class PedigreeReportClient:
    SAFE_RESPONSE_HEADERS = [
        "Content-Disposition",
        "Cache-Control",
        "ETag",
        "Expires",
        "Last-Modified",
        "Pragma",
    ]

    def get_health(self):
        import requests

        url = self._build_url("health")
        started_at = perf_counter()
        response = requests.get(
            url,
            headers={"Accept": "application/json"},
            timeout=self._timeout(),
            allow_redirects=False,
        )
        logger.info(
            "Pedigree health provider response.",
            extra={
                "operation": "health",
                "upstream_status_code": response.status_code,
                "duration_seconds": round(perf_counter() - started_at, 3),
            },
        )
        return response

    def get_reports(self):
        import requests

        url = self._build_url("api/reports/fastreport/reports")
        started_at = perf_counter()
        response = requests.get(
            url,
            headers=self._headers(),
            timeout=self._timeout(),
            allow_redirects=False,
        )
        logger.info(
            "Pedigree reports-list provider response.",
            extra={
                "operation": "reports-list",
                "upstream_status_code": response.status_code,
                "duration_seconds": round(perf_counter() - started_at, 3),
            },
        )
        return response

    def get_schema(self, *, shape, generation):
        import requests

        url = self._build_url(f"api/reports/fastreport/schema/{shape}")
        started_at = perf_counter()
        response = requests.get(
            url,
            params={"generation": generation},
            headers=self._headers(),
            timeout=self._timeout(),
            allow_redirects=False,
        )
        logger.info(
            "Pedigree schema provider response.",
            extra={
                "operation": "schema",
                "shape": shape,
                "generation": generation,
                "upstream_status_code": response.status_code,
                "duration_seconds": round(perf_counter() - started_at, 3),
            },
        )
        return response

    def get_style_parameters(self, *, shape, generation):
        import requests

        url = self._build_url("api/reports/fastreport/style-parameters")
        started_at = perf_counter()
        response = requests.get(
            url,
            params={"pedigreeType": shape, "generation": generation},
            headers=self._headers(),
            timeout=self._timeout(),
            allow_redirects=False,
        )
        logger.info(
            "Pedigree style-parameters provider response.",
            extra={
                "operation": "style-parameters",
                "shape": shape,
                "generation": generation,
                "upstream_status_code": response.status_code,
                "duration_seconds": round(perf_counter() - started_at, 3),
            },
        )
        return response

    def post_pdf(self, *, pdf_id, payload, accept):
        import requests

        url = self._build_url(f"api/reports/fastreport/pdf/{pdf_id}")
        started_at = perf_counter()
        response = requests.post(
            url,
            json=payload,
            headers=self._headers(
                accept=accept or "application/pdf",
                content_type="application/json",
            ),
            timeout=self._timeout(),
            allow_redirects=False,
        )
        logger.info(
            "Pedigree generate provider response.",
            extra={
                "operation": "generate",
                "pdf_id": pdf_id,
                "upstream_status_code": response.status_code,
                "duration_seconds": round(perf_counter() - started_at, 3),
            },
        )
        return response

    def _build_url(self, path):
        base_url = self._base_url()
        url = urljoin(f"{base_url}/", path)
        if urlparse(url).netloc != urlparse(base_url).netloc:
            raise ValidationError({"report": _("Unexpected report host.")})
        return url

    def _headers(self, *, accept="application/json", content_type=None):
        headers = {"Accept": accept}
        if content_type:
            headers["Content-Type"] = content_type
        headers["X-Api-Key"] = self._api_key()
        return headers

    @staticmethod
    def _base_url():
        base_url = getattr(settings, "PEDIGREE_REPORT_BASE_URL", "")
        base_url = (base_url or "").rstrip("/")
        if not base_url:
            raise PedigreeReportConfigurationError(
                _("PEDIGREE_REPORT_BASE_URL is not configured.")
            )
        return base_url

    @staticmethod
    def _api_key():
        api_key = getattr(settings, "PEDIGREE_REPORT_API_KEY", "")
        api_key = (api_key or "").strip()
        if not api_key:
            raise PedigreeReportConfigurationError(
                _("PEDIGREE_REPORT_API_KEY is not configured.")
            )
        return api_key

    @staticmethod
    def _timeout():
        return getattr(settings, "PEDIGREE_REPORT_TIMEOUT", 120)


class PedigreeReportGenerationService:
    @classmethod
    def build_provider_payload(
        cls,
        *,
        subject_bird_id,
        actor_profile,
        generation_depth,
        report,
        owner=None,
        customization=None,
    ):
        subject_bird = cls._get_subject_bird(subject_bird_id, actor_profile)
        logger.info(
            "Building pedigree generate payload.",
            extra={
                "operation": "generate",
                "subject_bird_id": subject_bird.pk,
                "generation_depth": generation_depth,
            },
        )
        return PedigreeReportMapper.merge_payload(
            frontend_report=report,
            subject_bird=subject_bird,
            generation_depth=generation_depth,
            owner=owner,
            customization=customization,
        )

    @staticmethod
    def _get_subject_bird(subject_bird_id, actor_profile):
        queryset = PedigreeBird.objects.select_related(
            "owner",
            "father",
            "mother",
        )
        if actor_profile.type != ProfileType.MANAGER:
            queryset = queryset.filter(owner_id=actor_profile.pk)
        try:
            return queryset.get(pk=subject_bird_id, deleted__isnull=True)
        except PedigreeBird.DoesNotExist as exc:
            raise NotFound(_("Subject bird was not found.")) from exc


class PedigreeImageBoxDetectionService:
    def detect_boxes(self, image_file):
        image = self._load_image(image_file)
        mask = self._build_colored_box_mask(image)
        contours = self._find_contours(mask)
        boxes = []
        image_height, image_width = image.shape[:2]

        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)

            if not self._is_valid_box(
                x=x,
                y=y,
                width=width,
                height=height,
                image_width=image_width,
                image_height=image_height,
            ):
                continue

            boxes.append(
                {
                    "bbox": self._normalize_bbox(
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                        image_width=image_width,
                        image_height=image_height,
                    ),
                    "lines": [],
                }
            )

        boxes = self._sort_boxes(boxes)

        for index, box in enumerate(boxes, start=1):
            box["box_id"] = f"b{index}"

        return boxes

    def _load_image(self, image_file):
        if hasattr(image_file, "open"):
            image_file.open("rb")

        image = Image.open(image_file).convert("RGB")
        image_array = np.array(image)

        return cv2.cvtColor(image_array, cv2.COLOR_RGB2BGR)

    def _build_colored_box_mask(self, image):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        lower = np.array([0, 20, 80])
        upper = np.array([179, 255, 255])

        mask = cv2.inRange(hsv, lower, upper)

        kernel = np.ones((9, 9), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        return mask

    def _find_contours(self, mask):
        contours, _ = cv2.findContours(
            mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        return contours

    def _is_valid_box(
        self,
        *,
        x,
        y,
        width,
        height,
        image_width,
        image_height,
    ):
        min_width = image_width * 0.06
        min_height = image_height * 0.05
        max_width = image_width * 0.45
        max_height = image_height * 0.75

        if width < min_width:
            return False

        if height < min_height:
            return False

        if width > max_width:
            return False

        if height > max_height:
            return False

        return True

    def _normalize_bbox(
        self,
        *,
        x,
        y,
        width,
        height,
        image_width,
        image_height,
    ):
        return {
            "x0": x / image_width,
            "y0": y / image_height,
            "x1": (x + width) / image_width,
            "y1": (y + height) / image_height,
        }

    def _sort_boxes(self, boxes):
        return sorted(
            boxes,
            key=lambda box: (
                box["bbox"]["x0"],
                box["bbox"]["y0"],
            ),
        )

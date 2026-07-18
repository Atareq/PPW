from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlparse

from PIL import Image, ImageEnhance, ImageOps
import cv2
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import numpy as np
import pytesseract
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.assets.models import Bird
from apps.pedigrees.constants import PEDIGREE_MAX_GENERATION_DEPTH
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeBirdSex,
    PedigreeCertificate,
    PedigreeCertificateStatus,
    PedigreeDocument,
    PedigreeDocumentStatus,
    PedigreeTemplate,
    normalize_ring_number,
)
from apps.users.constants import ProfileType


class PedigreeExtractionError(ValueError):
    pass


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
            raise ValidationError(
                {"birthday": _("Birthday cannot be in the future.")}
            )
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
                {
                    "ring_number": _(
                        "A live pedigree bird with this ring number exists."
                    )
                }
            )

    @classmethod
    def _validate_parents(cls, data, *, owner, instance=None):
        father = data.get("father")
        mother = data.get("mother")

        if father and instance and father.pk == instance.pk:
            raise ValidationError(
                {"father": _("A bird cannot be its own father.")}
            )
        if mother and instance and mother.pk == instance.pk:
            raise ValidationError(
                {"mother": _("A bird cannot be its own mother.")}
            )
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
                if (
                    parent
                    and parent.birthday
                    and parent.birthday >= child_birthday
                ):
                    raise ValidationError(
                        {
                            field: _(
                                "Parent birthday must be before child birthday."
                            )
                        }
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
    def save_reviewed_pedigree(
        cls, *, document_id, actor_profile, reviewed_nodes
    ):
        document = PedigreeDocumentService.lock_document_for_actor(
            document_id,
            actor_profile,
        )
        owner = document.owner
        PedigreeCertificateService._validate_profile_owner(owner)

        merged_by_ring, path_to_ring = cls._merge_nodes_by_normalized_ring(
            reviewed_nodes
        )
        birds_by_ring, created_ids, scalar_updated_ids = (
            cls._create_or_update_birds(
                owner=owner,
                merged_by_ring=merged_by_ring,
            )
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
        existing_ids = {
            bird.pk for bird in birds_by_ring.values()
        } - created_ids
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
                raise ValidationError(
                    {"ring_number": _("Ring number is required.")}
                )
            path_to_ring[node["path"]] = normalized_ring

            if normalized_ring not in merged_by_ring:
                merged_by_ring[normalized_ring] = {
                    field: node[field]
                    for field in cls.SCALAR_FIELDS
                    if field in node
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
                    attrs={
                        field: getattr(bird, field) for field in changed_fields
                    },
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
                desired = desired_by_child.setdefault(
                    child.pk, {"bird": child}
                )
                existing_parent = desired.get(relation)
                if (
                    existing_parent is not None
                    and existing_parent.pk != parent.pk
                ):
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


class PedigreeTemplateAccessService:
    """Placeholder entitlement hook for future premium-template checks."""

    @staticmethod
    def available_templates_for_profile(profile):
        return PedigreeTemplate.objects.filter(
            is_active=True,
            pedigree_type__gt="",
            report_name__gt="",
            generation__isnull=False,
        ).order_by("sort_order", "id")

    @staticmethod
    def validate_available(template, profile):
        if not template.is_configured:
            raise ValidationError(
                {"template_id": _("Template is not available.")}
            )
        return template


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
        owner=None,
        customization=None,
    ):
        # generation_depth is the total number of levels, with the subject at
        # depth 1. A depth of 2 includes only the subject and direct parents.
        return {
            "owner": cls._owner_data(subject_bird.owner, owner or {}),
            "customization": cls._customization_data(customization or {}),
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
            raise ValidationError(
                {"subject": _("Pedigree tree contains a cycle.")}
            )
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
    def _owner_data(profile, supplied, current=None):
        current = current or {}
        user = profile.user
        display_name = (
            " ".join(
                part for part in [user.first_name, user.last_name] if part
            ).strip()
            or user.username
        )
        seller = getattr(profile, "sellerprofile", None)
        loft_name = getattr(seller, "nickname", "") or display_name
        defaults = {
            "name": current.get("name", display_name),
            "loft_name": current.get("loft_name", loft_name),
            "phone": current.get("phone", str(user.phone_number or "")),
            "whatsapp": current.get("whatsapp", str(user.phone_number or "")),
            "email": current.get("email", user.email or ""),
        }
        return {
            "name": supplied.get("name", defaults["name"]) or "",
            "loft_name": supplied.get("loft_name", defaults["loft_name"])
            or "",
            "phone": supplied.get("phone", defaults["phone"]) or "",
            "whatsapp": supplied.get("whatsapp", defaults["whatsapp"]) or "",
            "email": supplied.get("email", defaults["email"]) or "",
        }

    @staticmethod
    def _customization_data(supplied, current=None):
        current = current or {}
        return {
            "background_image": supplied.get(
                "background_image",
                current.get("background_image", ""),
            )
            or "",
            "bird_image": supplied.get(
                "bird_image", current.get("bird_image", "")
            )
            or "",
            "compuclub": supplied.get(
                "compuclub",
                current.get("compuclub", "Pigeon Planet"),
            )
            or "Pigeon Planet",
        }

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
    @classmethod
    def build_payload(cls, *, certificate, template, pedigree_data):
        lookup = PedigreeTreeService.build_path_lookup(
            pedigree_data,
            max_generation=template.generation,
        )
        subject = lookup.get("") or {}
        owner = pedigree_data.get("owner") or {}
        customization = pedigree_data.get("customization") or {}
        filename = cls.safe_filename(certificate, subject)
        parameters = {
            "Farkh_No": cls._ring(subject),
            "Farth_No": cls._ring(subject),
            "Esmah_FullNo": cls._text(subject),
            "Farkh_Image": customization.get("bird_image", "") or "",
            "Farth_Name": owner.get("name", "") or "",
            "OwnerLoftName": owner.get("loft_name", "") or "",
            "Owner_LoftName": owner.get("loft_name", "") or "",
            "OwnerPhone": owner.get("phone", "") or "",
            "OwnerWhatsApp": owner.get("whatsapp", "") or "",
            "OwnerEmail": owner.get("email", "") or "",
            "BackgroundImage": customization.get("background_image", "") or "",
            "Compuclub": customization.get("compuclub", "Pigeon Planet")
            or "Pigeon Planet",
            "PrintDate": timezone.localdate().isoformat(),
        }
        parameters["Farth_FatherNo"] = cls._ring(lookup.get("F"))
        parameters["Father_Info"] = cls._text(lookup.get("F"))
        parameters["Farth_MotherNo"] = cls._ring(lookup.get("M"))
        parameters["Mother_Info"] = cls._text(lookup.get("M"))
        for path, (ring_field, text_field) in ANCESTOR_FIELD_MAPPING.items():
            node = lookup.get(path)
            parameters[ring_field] = cls._ring(node)
            parameters[text_field] = cls._text(node)

        return {
            "pedigreeType": str(template.pedigree_type),
            "reportName": template.report_name,
            "generation": str(template.generation),
            "fileName": filename,
            "parameters": parameters,
            "dataSources": [],
        }

    @staticmethod
    def safe_filename(certificate, subject):
        ring = normalize_ring_number(
            (subject or {}).get("ring_number", "bird")
        )
        slug = "".join(
            ch.lower() if ch.isalnum() else "-" for ch in ring
        ).strip("-")
        slug = "-".join(part for part in slug.split("-") if part) or "bird"
        return f"pedigree-{slug}-{certificate.pk}-r{certificate.generation_revision}"

    @staticmethod
    def _ring(node):
        return (node or {}).get("ring_number", "") or ""

    @staticmethod
    def _text(node):
        node = node or {}
        return node.get("name", "") or node.get("description", "") or ""


@dataclass
class PedigreeReportResponse:
    content: bytes
    mime_type: str
    status_code: int
    extension: str


class TemporaryPedigreeReportError(APIException):
    status_code = 503
    default_code = "temporary_report_error"


class PedigreeReportClient:
    ALLOWED_MIME_TYPES = {
        "application/pdf": (b"%PDF-", ".pdf"),
        "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
        "image/jpeg": (b"\xff\xd8", ".jpg"),
    }
    RETRYABLE_STATUS_CODES = {502, 503, 504}

    def post_report(self, *, external_pdf_id, payload):
        import requests

        base_url = settings.PEDIGREE_REPORT_BASE_URL.rstrip("/")
        url = urljoin(
            f"{base_url}/", f"api/reports/rdlc/pdf/{external_pdf_id}"
        )
        if urlparse(url).netloc != urlparse(base_url).netloc:
            raise ValidationError({"report": _("Unexpected report host.")})

        headers = {}
        api_key = getattr(settings, "PEDIGREE_REPORT_API_KEY", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=(
                    settings.PEDIGREE_REPORT_CONNECT_TIMEOUT,
                    settings.PEDIGREE_REPORT_READ_TIMEOUT,
                ),
                allow_redirects=False,
            )
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:
            raise TemporaryPedigreeReportError(
                _("Temporary pedigree report service failure.")
            ) from exc
        if 300 <= response.status_code < 400:
            raise ValidationError(
                {"report": _("Unexpected pedigree report redirect.")}
            )
        if response.status_code in self.RETRYABLE_STATUS_CODES:
            raise TemporaryPedigreeReportError(
                _("Temporary pedigree report service failure.")
            )
        if response.status_code >= 400:
            raise ValidationError(
                {"report": _("Pedigree report service rejected the request.")}
            )
        return self.validate_response(
            content=response.content,
            content_type=response.headers.get("content-type", ""),
            status_code=response.status_code,
        )

    @classmethod
    def validate_response(cls, *, content, content_type, status_code=200):
        if not content:
            raise ValidationError(
                {"report": _("Pedigree report response was empty.")}
            )
        max_size = settings.PEDIGREE_REPORT_MAX_FILE_SIZE_MB * 1024 * 1024
        if len(content) > max_size:
            raise ValidationError(
                {"report": _("Pedigree report response was too large.")}
            )
        mime_type = (content_type or "").split(";")[0].strip().lower()
        if mime_type not in cls.ALLOWED_MIME_TYPES:
            raise ValidationError(
                {"report": _("Unsupported pedigree report MIME type.")}
            )
        signature, extension = cls.ALLOWED_MIME_TYPES[mime_type]
        if not content.startswith(signature):
            raise ValidationError(
                {"report": _("Pedigree report file signature is invalid.")}
            )
        return PedigreeReportResponse(
            content=content,
            mime_type=mime_type,
            status_code=status_code,
            extension=extension,
        )


class PedigreeCertificateService:
    TRANSIENT_ERROR_FIELDS = {
        "error_code": "",
        "error_message": "",
        "external_status_code": None,
    }

    @classmethod
    @transaction.atomic
    def create_certificate(
        cls,
        *,
        requested_by,
        owner,
        subject_bird,
        template,
        generation_depth,
        owner_data=None,
        customization=None,
    ):
        cls._validate_profile_owner(owner)
        cls._validate_subject(subject_bird, owner)
        PedigreeTemplateAccessService.validate_available(template, owner)
        cls._validate_generation_depth(
            generation_depth=generation_depth,
            template=template,
        )
        normalized_owner_data = PedigreeTreeService._owner_data(
            owner,
            owner_data or {},
        )
        normalized_customization = PedigreeTreeService._customization_data(
            customization or {},
        )
        certificate = PedigreeCertificate.objects.create(
            owner=owner,
            requested_by=requested_by,
            subject_bird=subject_bird,
            template=template,
            status=PedigreeCertificateStatus.PENDING,
            generation_depth=generation_depth,
            owner_data=normalized_owner_data,
            customization=normalized_customization,
            generation_revision=1,
        )
        cls._queue_generation(certificate)
        return certificate

    @classmethod
    @transaction.atomic
    def regenerate_certificate(
        cls,
        *,
        certificate,
        template=None,
        generation_depth=None,
        owner_data=None,
        customization=None,
        actor_profile,
    ):
        if (
            actor_profile.type != ProfileType.MANAGER
            and certificate.owner_id != actor_profile.pk
        ):
            raise NotFound(_("Pedigree certificate was not found."))
        if certificate.status == PedigreeCertificateStatus.PROCESSING:
            raise ValidationError(
                {"status": _("Certificate is currently processing.")}
            )
        effective_template = template or certificate.template
        effective_generation_depth = (
            generation_depth or certificate.generation_depth
        )
        PedigreeTemplateAccessService.validate_available(
            effective_template,
            actor_profile,
        )
        cls._validate_generation_depth(
            generation_depth=effective_generation_depth,
            template=effective_template,
        )
        if owner_data is not None:
            certificate.owner_data = PedigreeTreeService._owner_data(
                certificate.owner,
                owner_data,
                current=certificate.owner_data,
            )
        if customization is not None:
            certificate.customization = (
                PedigreeTreeService._customization_data(
                    customization,
                    current=certificate.customization,
                )
            )
        certificate.template = effective_template
        certificate.generation_depth = effective_generation_depth
        certificate.status = PedigreeCertificateStatus.PENDING
        certificate.generation_revision += 1
        certificate.error_code = ""
        certificate.error_message = ""
        certificate.external_status_code = None
        certificate.save(
            update_fields=[
                "template",
                "generation_depth",
                "owner_data",
                "customization",
                "status",
                "generation_revision",
                "error_code",
                "error_message",
                "external_status_code",
                "modified",
            ]
        )
        cls._queue_generation(certificate)
        return certificate

    @classmethod
    def generate_certificate(
        cls, *, certificate_id, expected_revision, client=None
    ):
        prepared = cls._prepare_generation(
            certificate_id=certificate_id,
            expected_revision=expected_revision,
        )
        if prepared is None:
            return "stale"
        certificate_id, payload = prepared
        client = client or PedigreeReportClient()
        try:
            response = client.post_report(
                external_pdf_id=payload["_external_pdf_id"],
                payload=payload["body"],
            )
        except TemporaryPedigreeReportError:
            cls._mark_failed(
                certificate_id=certificate_id,
                expected_revision=expected_revision,
                code="temporary_report_error",
                message="Temporary pedigree report service failure.",
            )
            raise
        except Exception as exc:
            cls._mark_failed(
                certificate_id=certificate_id,
                expected_revision=expected_revision,
                code=cls._error_code(exc),
                message=str(exc),
            )
            return "failed"
        cls._mark_completed(
            certificate_id=certificate_id,
            expected_revision=expected_revision,
            response=response,
            filename_base=payload["body"]["fileName"],
        )
        return "completed"

    @classmethod
    @transaction.atomic
    def _prepare_generation(cls, *, certificate_id, expected_revision):
        try:
            certificate = (
                PedigreeCertificate.objects.select_for_update()
                .select_related(
                    "template", "subject_bird", "owner", "requested_by"
                )
                .get(pk=certificate_id)
            )
        except PedigreeCertificate.DoesNotExist:
            return None
        if certificate.generation_revision != expected_revision:
            return None
        if (
            certificate.status == PedigreeCertificateStatus.COMPLETED
            and certificate.generated_file
        ):
            return None
        if certificate.subject_bird is None:
            raise ValidationError(
                {"subject_bird_id": _("Subject bird was not found.")}
            )
        cls._validate_generation_depth(
            generation_depth=certificate.generation_depth,
            template=certificate.template,
        )
        pedigree_data = PedigreeTreeService.build_generation_data(
            subject_bird=certificate.subject_bird,
            generation_depth=certificate.generation_depth,
            owner=certificate.owner_data,
            customization=certificate.customization,
        )
        payload = PedigreeReportMapper.build_payload(
            certificate=certificate,
            template=certificate.template,
            pedigree_data=pedigree_data,
        )
        certificate.status = PedigreeCertificateStatus.PROCESSING
        certificate.external_request_payload = payload
        certificate.error_code = ""
        certificate.error_message = ""
        certificate.external_status_code = None
        certificate.save(
            update_fields=[
                "status",
                "external_request_payload",
                "error_code",
                "error_message",
                "external_status_code",
                "modified",
            ]
        )
        return certificate.pk, {
            "_external_pdf_id": certificate.template.external_pdf_id,
            "body": payload,
        }

    @classmethod
    @transaction.atomic
    def _mark_completed(
        cls, *, certificate_id, expected_revision, response, filename_base
    ):
        try:
            certificate = PedigreeCertificate.objects.select_for_update().get(
                pk=certificate_id
            )
        except PedigreeCertificate.DoesNotExist:
            return
        if certificate.generation_revision != expected_revision:
            return
        filename = f"{filename_base}{response.extension}"
        certificate.generated_file.save(
            filename,
            ContentFile(response.content),
            save=False,
        )
        certificate.generated_filename = filename
        certificate.mime_type = response.mime_type
        certificate.external_status_code = response.status_code
        certificate.status = PedigreeCertificateStatus.COMPLETED
        certificate.generated_at = timezone.now()
        certificate.error_code = ""
        certificate.error_message = ""
        certificate.save(
            update_fields=[
                "generated_file",
                "generated_filename",
                "mime_type",
                "external_status_code",
                "status",
                "generated_at",
                "error_code",
                "error_message",
                "modified",
            ]
        )

    @classmethod
    @transaction.atomic
    def _mark_failed(cls, *, certificate_id, expected_revision, code, message):
        try:
            certificate = PedigreeCertificate.objects.select_for_update().get(
                pk=certificate_id
            )
        except PedigreeCertificate.DoesNotExist:
            return
        if certificate.generation_revision != expected_revision:
            return
        certificate.status = PedigreeCertificateStatus.FAILED
        certificate.error_code = code[:100]
        certificate.error_message = str(message)[:1000]
        certificate.generated_at = None
        certificate.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "generated_at",
                "modified",
            ]
        )

    @staticmethod
    def _validate_profile_owner(owner):
        if owner.type not in {ProfileType.CUSTOMER, ProfileType.SELLER}:
            raise ValidationError(
                {
                    "owner": _(
                        "Only active customer or seller profiles can generate "
                        "certificates."
                    )
                }
            )

    @staticmethod
    def _validate_subject(subject_bird, owner):
        if subject_bird.owner_id != owner.pk:
            raise ValidationError(
                {"subject_bird_id": _("Subject bird was not found.")}
            )

    @staticmethod
    def _validate_generation_depth(*, generation_depth, template):
        if generation_depth < 1:
            raise ValidationError(
                {"generation_depth": _("Generation depth must be at least 1.")}
            )
        if generation_depth > PEDIGREE_MAX_GENERATION_DEPTH:
            raise ValidationError(
                {
                    "generation_depth": _(
                        "Generation depth exceeds the platform maximum."
                    )
                }
            )
        if template.generation and generation_depth > template.generation:
            raise ValidationError(
                {
                    "generation_depth": _(
                        "Generation depth cannot exceed the selected template "
                        "generation."
                    )
                }
            )

    @staticmethod
    def _queue_generation(certificate):
        from apps.pedigrees.tasks import generate_pedigree_certificate

        transaction.on_commit(
            lambda: generate_pedigree_certificate.delay(
                certificate.pk,
                certificate.generation_revision,
            )
        )

    @staticmethod
    def _error_code(exc):
        if isinstance(exc, ValidationError):
            return "invalid_report_response"
        return exc.__class__.__name__.lower()[:100]


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

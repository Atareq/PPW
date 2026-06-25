from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.assets.models import Bird
from apps.pedigrees.models import PedigreeDocument, PedigreeDocumentStatus
from apps.users.constants import ProfileType
try:
    import cv2
    import numpy as np
    from PIL import Image, ImageOps, ImageEnhance
    import pytesseract
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False


class PedigreeExtractionError(ValueError):
    pass


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
            print(boxes)
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
        bird_ring_number,
        description,
    ):
        document = PedigreeDocumentService._get_locked_document_for_actor(
            document_id,
            actor_profile,
        )
        document.reviewed_bird_ring_number = bird_ring_number
        document.reviewed_description = description
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
        # TODO: Update the linked Bird when a dedicated pedigree description
        # field exists; do not overwrite the generic Asset.description here.
        return PedigreeDocumentService.get_document(document.pk)

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
            raise NotFound("Bird was not found.") from exc

    @staticmethod
    def _get_locked_document_for_actor(document_id, actor_profile):
        queryset = PedigreeDocument.objects.select_for_update()
        if actor_profile.type != ProfileType.MANAGER:
            queryset = queryset.filter(owner_id=actor_profile.pk)

        try:
            return queryset.get(pk=document_id)
        except PedigreeDocument.DoesNotExist as exc:
            raise NotFound("Pedigree document was not found.") from exc


class PedigreeBoxOCRService:
    def extract_lines_for_boxes(self, image_file, boxes):
        if not _OCR_AVAILABLE:
            raise PedigreeExtractionError("OCR dependencies are not available in this environment.")
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


class PedigreeImageBoxDetectionService:
    def detect_boxes(self, image_file):
        if not _OCR_AVAILABLE:
            raise PedigreeExtractionError("OCR dependencies are not available in this environment.")
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

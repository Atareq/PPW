from django.conf import settings
from django.utils import translation


class ApiLocaleMiddleware:
    """Activate the API language from ?lang=, Accept-Language, or Arabic default."""

    supported_languages = {"ar", "en"}
    default_language = "ar"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = self._select_language(request)
        translation.activate(language)
        request.LANGUAGE_CODE = language

        try:
            response = self.get_response(request)
            response.headers.setdefault("Content-Language", language)
            return response
        finally:
            translation.deactivate()

    @classmethod
    def _select_language(cls, request) -> str:
        query_language = request.GET.get("lang")
        normalized_query_language = cls._normalize_language(query_language)
        if normalized_query_language:
            return normalized_query_language

        header_language = request.META.get("HTTP_ACCEPT_LANGUAGE")
        normalized_header_language = cls._normalize_accept_language(header_language)
        if normalized_header_language:
            return normalized_header_language

        return getattr(settings, "LANGUAGE_CODE", cls.default_language).split("-")[0]

    @classmethod
    def _normalize_accept_language(cls, value: str | None) -> str | None:
        if not value:
            return None
        for language_range in value.split(","):
            language = language_range.split(";", 1)[0].strip()
            normalized_language = cls._normalize_language(language)
            if normalized_language:
                return normalized_language
        return None

    @classmethod
    def _normalize_language(cls, value: str | None) -> str | None:
        if not value:
            return None
        language = value.strip().lower().replace("_", "-").split("-", 1)[0]
        if language in cls.supported_languages:
            return language
        return None

from django_extensions.db.models import TimeStampedModel
from safedelete.models import SafeDeleteModel


class BaseModel(TimeStampedModel, SafeDeleteModel):
    class Meta:
        abstract = True

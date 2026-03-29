from django.db import models
from django_extensions.db.models import TimeStampedModel
from safedelete.models import SafeDeleteModel



class BaseModel(TimeStampedModel, SafeDeleteModel):
    class Meta:
        abstract = True



class OwnedEntityModel(BaseModel):
    owner = models.ForeignKey(
        'authentication.UserAccount', on_delete=models.CASCADE,
        related_name="owned_%(class)ss",
        db_index=True
    )

    class Meta:
        abstract = True


class ResourcePermission(models.Model):
    resource = models.CharField(max_length=100, unique=True)
    owner = models.ForeignKey(
        'authentication.UserAccount', on_delete=models.CASCADE,
        db_index=True
    )
    def __str__(self):
        return self.resource
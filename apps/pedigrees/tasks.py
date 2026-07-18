from celery import shared_task

from apps.pedigrees.services import (
    PedigreeCertificateService,
    TemporaryPedigreeReportError,
)


@shared_task(
    bind=True,
    name="pedigrees.tasks.generate_pedigree_certificate",
    autoretry_for=(TemporaryPedigreeReportError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def generate_pedigree_certificate(self, certificate_id, expected_revision):
    """Retry-safe wrapper for asynchronous certificate generation."""
    return PedigreeCertificateService.generate_certificate(
        certificate_id=certificate_id,
        expected_revision=expected_revision,
    )

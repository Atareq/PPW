from celery import shared_task

from apps.auctions.services import AuctionService


@shared_task(name="auctions.tasks.close_expired_auctions")
def close_expired_auctions():
    """Thin Celery wrapper around auction auto-closing orchestration."""
    return AuctionService.close_expired_auctions()

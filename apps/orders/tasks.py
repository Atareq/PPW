from celery import shared_task

from apps.orders.services import OrderItemService


@shared_task(name="orders.tasks.cancel_expired_pending_order_items")
def cancel_expired_pending_order_items():
    """Thin Celery wrapper around pending seller decision timeout cleanup."""
    return OrderItemService.cancel_expired_pending_items()

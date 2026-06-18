from celery import shared_task

from apps.loyalty.reward_engine import RewardEngine


@shared_task(name="loyalty.tasks.evaluate_trusted_buyer")
def evaluate_trusted_buyer(profile_id):
    return RewardEngine.evaluate_trusted_buyer(profile_id)


@shared_task(name="loyalty.tasks.evaluate_trusted_seller")
def evaluate_trusted_seller(profile_id):
    return RewardEngine.evaluate_trusted_seller(profile_id)

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.general.models import BaseModel
from apps.loyalty.constants import BadgeType, PointTransactionType, RewardRuleCode


class PointWallet(BaseModel):
    user = models.OneToOneField(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="point_wallet",
    )
    balance = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(balance__gte=0),
                name="loyalty_wallet_balance_gte_0",
            ),
        ]

    def __str__(self):
        return f"PointWallet #{self.pk} user={self.user_id}"


class PointTransaction(BaseModel):
    wallet = models.ForeignKey(
        PointWallet,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.CASCADE,
        related_name="point_transactions",
        db_index=True,
    )
    profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.SET_NULL,
        related_name="point_transactions",
        null=True,
        blank=True,
    )
    transaction_type = models.CharField(
        max_length=10,
        choices=PointTransactionType.choices,
    )
    points = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    balance_after = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    reason = models.TextField()
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=100, blank=True)
    idempotency_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created"]),
            models.Index(fields=["profile", "-created"]),
            models.Index(fields=["wallet", "-created"]),
            models.Index(fields=["transaction_type"]),
            models.Index(fields=["source_type"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(points__gt=0),
                name="loyalty_transaction_points_gt_0",
            ),
            models.CheckConstraint(
                check=Q(balance_after__gte=0),
                name="loyalty_transaction_balance_after_gte_0",
            ),
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=(Q(idempotency_key__isnull=False) & ~Q(idempotency_key="")),
                name="loyalty_unique_transaction_idempotency_key",
            ),
        ]

    def __str__(self):
        return f"PointTransaction #{self.pk} " f"{self.transaction_type} {self.points}"


class BadgeAward(BaseModel):
    profile = models.ForeignKey(
        "users.Profile",
        on_delete=models.CASCADE,
        related_name="badge_awards",
    )
    badge_type = models.CharField(
        max_length=50,
        choices=BadgeType.choices,
    )
    awarded_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=100, blank=True)
    idempotency_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["profile", "-awarded_at"]),
            models.Index(fields=["badge_type"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["source_type"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=(Q(idempotency_key__isnull=False) & ~Q(idempotency_key="")),
                name="loyalty_unique_badge_idempotency_key",
            ),
        ]

    @property
    def is_active(self):
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    def __str__(self):
        return f"BadgeAward #{self.pk} {self.badge_type}"


class RewardRuleConfig(BaseModel):
    """Manager override of one system-defined reward rule's tunable values.

    A row exists only after a manager changes the rule; otherwise the canonical
    default in ``DEFAULT_REWARD_RULES`` applies. Read it through
    ``RewardRuleService``, never directly.
    """

    code = models.CharField(max_length=50, choices=RewardRuleCode.choices)
    enabled = models.BooleanField(default=True)
    points = models.PositiveIntegerField(null=True, blank=True)
    multiplier = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    threshold = models.PositiveIntegerField(null=True, blank=True)
    configuration = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=Q(deleted__isnull=True),
                name="loyalty_unique_live_reward_rule_code",
            ),
            models.CheckConstraint(
                check=Q(multiplier__isnull=True) | Q(multiplier__gte=0),
                name="loyalty_reward_rule_multiplier_gte_0",
            ),
        ]

    def __str__(self):
        return f"RewardRuleConfig {self.code}"

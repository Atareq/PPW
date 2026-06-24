import base64
import json

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.db.models.functions import Random
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.assets.models import Asset, AssetCategory, AssetStatus
from apps.auctions.models import Auction, AuctionStatus
from apps.feed.models import SellerCustomerBlock, SellerFollow, SellerPackageFollow
from apps.packages.models import SellerPackage, SellerPackageStatus
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, Profile, SellerProfile
from apps.orders.models import OrderItemStatus


DEFAULT_FEED_LIMIT = 20
DEFAULT_SELLER_SUGGESTION_LIMIT = 10
MAX_FEED_LIMIT = 50
PRODUCT_MARKET_CATEGORIES = [
    AssetCategory.SUPPLY,
    AssetCategory.ACCESSORY,
    AssetCategory.FEED,
    AssetCategory.SUPPLEMENT,
]


class BlockService:
    @staticmethod
    @transaction.atomic
    def block_customer_seller(actor_profile, other_profile, reason=None):
        actor = BlockService._require_active_customer_or_seller(actor_profile)
        other = BlockService._get_profile(other_profile)
        customer, seller = BlockService._resolve_customer_seller(actor, other)

        try:
            (
                block,
                created,
            ) = SellerCustomerBlock.objects.select_for_update().get_or_create(
                customer_id=customer.pk,
                seller_id=seller.pk,
                defaults={
                    "blocked_by_id": actor.pk,
                    "reason": reason,
                    "is_active": True,
                },
            )
        except IntegrityError:
            block = SellerCustomerBlock.objects.select_for_update().get(
                customer_id=customer.pk,
                seller_id=seller.pk,
            )
            created = False

        if not created and not block.is_active:
            block.blocked_by_id = actor.pk
            block.reason = reason
            block.is_active = True
            block.save(
                update_fields=[
                    "blocked_by",
                    "reason",
                    "is_active",
                    "modified",
                ]
            )

        BlockService._remove_follow(customer, seller)
        BlockService._remove_package_follows(customer, seller)
        return block

    @staticmethod
    @transaction.atomic
    def unblock_customer_seller(actor_profile, other_profile):
        actor = BlockService._require_active_customer_or_seller(actor_profile)
        other = BlockService._get_profile(other_profile)
        customer, seller = BlockService._resolve_customer_seller(actor, other)

        try:
            block = SellerCustomerBlock.objects.select_for_update().get(
                customer_id=customer.pk,
                seller_id=seller.pk,
                is_active=True,
            )
        except SellerCustomerBlock.DoesNotExist as exc:
            raise NotFound("Active block was not found.") from exc

        if block.blocked_by_id != actor.pk:
            raise PermissionDenied(
                "Only the profile that created the block can unblock."
            )

        block.is_active = False
        block.save(update_fields=["is_active", "modified"])
        return block

    @staticmethod
    def is_blocked_between(customer_profile, seller_profile):
        return SellerCustomerBlock.objects.filter(
            customer_id=getattr(customer_profile, "pk", customer_profile),
            seller_id=getattr(seller_profile, "pk", seller_profile),
            is_active=True,
        ).exists()

    @staticmethod
    def get_blocked_seller_ids_for_customer(customer_profile):
        return list(
            SellerCustomerBlock.objects.filter(
                customer_id=getattr(customer_profile, "pk", customer_profile),
                is_active=True,
            ).values_list("seller_id", flat=True)
        )

    @staticmethod
    def get_blocked_customer_ids_for_seller(seller_profile):
        return list(
            SellerCustomerBlock.objects.filter(
                seller_id=getattr(seller_profile, "pk", seller_profile),
                is_active=True,
            ).values_list("customer_id", flat=True)
        )

    @staticmethod
    def blocks_for_profile(active_profile):
        profile = BlockService._require_active_customer_or_seller(active_profile)
        queryset = SellerCustomerBlock.objects.filter(
            is_active=True,
        ).select_related(
            "customer",
            "seller",
            "blocked_by",
        )

        if profile.type == ProfileType.CUSTOMER:
            return queryset.filter(customer_id=profile.pk).order_by(
                "-created",
                "-id",
            )
        return queryset.filter(seller_id=profile.pk).order_by("-created", "-id")

    @staticmethod
    def _remove_follow(customer, seller):
        SellerFollow.objects.filter(
            follower_id=customer.pk,
            seller_id=seller.pk,
            is_active=True,
        ).update(is_active=False, modified=timezone.now())

    @staticmethod
    def _remove_package_follows(customer, seller):
        SellerPackageFollow.objects.filter(
            follower_id=customer.pk,
            seller_package__owner_id=seller.pk,
            is_active=True,
        ).update(is_active=False, modified=timezone.now())

    @staticmethod
    def _resolve_customer_seller(actor, other):
        if other.type not in {ProfileType.CUSTOMER, ProfileType.SELLER}:
            raise ValidationError(
                {
                    "profile_id": (
                        "Blocks are supported only between customers and sellers."
                    )
                }
            )
        if actor.type == other.type:
            raise ValidationError(
                {"profile_id": "Blocks must be between one customer and one seller."}
            )
        if actor.user_id == other.user_id:
            raise ValidationError(
                {
                    "profile_id": (
                        "Cannot block another customer or seller profile on your account."
                    )
                }
            )

        if actor.type == ProfileType.CUSTOMER:
            return (
                BlockService._get_customer(actor),
                BlockService._get_seller(other),
            )
        return (
            BlockService._get_customer(other),
            BlockService._get_seller(actor),
        )

    @staticmethod
    def _require_active_customer_or_seller(profile_or_id):
        profile = BlockService._get_profile(profile_or_id)
        if profile.type not in {ProfileType.CUSTOMER, ProfileType.SELLER}:
            raise PermissionDenied("Only customer and seller profiles can use blocks.")
        if (
            not profile.is_active
            or not profile.activated
            or not profile.is_valid
            or getattr(profile, "deleted", None) is not None
        ):
            raise PermissionDenied("Active profile is required.")
        return profile

    @staticmethod
    def _get_profile(profile_or_id):
        if hasattr(profile_or_id, "pk") and getattr(profile_or_id, "type", None):
            return profile_or_id

        try:
            return Profile.objects.get(pk=profile_or_id)
        except Profile.DoesNotExist as exc:
            raise NotFound("Profile was not found.") from exc

    @staticmethod
    def _get_customer(profile):
        try:
            return CustomerProfile.objects.get(pk=profile.pk)
        except CustomerProfile.DoesNotExist as exc:
            raise ValidationError(
                {"profile_id": "Customer profile was not found."}
            ) from exc

    @staticmethod
    def _get_seller(profile):
        try:
            return SellerProfile.objects.get(pk=profile.pk)
        except SellerProfile.DoesNotExist as exc:
            raise ValidationError(
                {"profile_id": "Seller profile was not found."}
            ) from exc


class SellerFollowService:
    @staticmethod
    def follow(active_profile, seller_id):
        customer = SellerFollowService._require_customer(active_profile)
        seller = SellerFollowService._get_seller(seller_id)
        SellerFollowService._validate_not_same_user(customer, seller)
        SellerFollowService._ensure_active_seller(seller)
        if BlockService.is_blocked_between(customer, seller):
            raise ValidationError(
                {"seller_id": "Cannot follow this seller while a block is active."}
            )

        try:
            with transaction.atomic():
                follow, created = SellerFollow.objects.get_or_create(
                    follower_id=customer.pk,
                    seller_id=seller.pk,
                    defaults={"is_active": True},
                )
                if not created and not follow.is_active:
                    follow.is_active = True
                    follow.save(update_fields=["is_active", "modified"])
                SellerFollowService._handle_follow_reward(follow, customer)
                return follow
        except IntegrityError:
            follow = SellerFollow.objects.get(
                follower_id=customer.pk,
                seller_id=seller.pk,
            )
            if not follow.is_active:
                follow.is_active = True
                follow.save(update_fields=["is_active", "modified"])
            SellerFollowService._handle_follow_reward(follow, customer)
            return follow

    @staticmethod
    def unfollow(active_profile, seller_id):
        customer = SellerFollowService._require_customer(active_profile)
        seller = SellerProfile.objects.filter(pk=seller_id).first()
        if seller is None:
            raise NotFound("Seller was not found.")

        SellerFollow.objects.filter(
            follower_id=customer.pk,
            seller_id=seller.pk,
            is_active=True,
        ).update(is_active=False, modified=timezone.now())
        return seller

    @staticmethod
    def following_queryset(active_profile):
        customer = SellerFollowService._require_customer(active_profile)
        blocked_seller_ids = BlockService.get_blocked_seller_ids_for_customer(customer)
        queryset = (
            SellerFollow.objects.filter(
                follower_id=customer.pk,
                is_active=True,
                seller__is_active=True,
                seller__activated=True,
                seller__is_valid=True,
                seller__deleted__isnull=True,
            )
            .select_related("seller")
            .order_by("-created", "-id")
        )
        if blocked_seller_ids:
            queryset = queryset.exclude(seller_id__in=blocked_seller_ids)
        return queryset

    @staticmethod
    def followed_seller_ids(customer_profile):
        blocked_seller_ids = BlockService.get_blocked_seller_ids_for_customer(
            customer_profile
        )
        queryset = SellerFollow.objects.filter(
            follower_id=customer_profile.pk,
            is_active=True,
            seller__is_active=True,
            seller__activated=True,
            seller__is_valid=True,
            seller__deleted__isnull=True,
        )
        if blocked_seller_ids:
            queryset = queryset.exclude(seller_id__in=blocked_seller_ids)
        return list(queryset.values_list("seller_id", flat=True))

    @staticmethod
    def is_following(customer_profile, seller_profile):
        return SellerFollow.objects.filter(
            follower_id=getattr(customer_profile, "pk", customer_profile),
            seller_id=getattr(seller_profile, "pk", seller_profile),
            is_active=True,
            seller__is_active=True,
            seller__activated=True,
            seller__is_valid=True,
            seller__deleted__isnull=True,
        ).exists()

    @staticmethod
    def _require_customer(profile):
        if profile.type != ProfileType.CUSTOMER:
            raise PermissionDenied("Active profile must be a customer.")
        return CustomerProfile.objects.get(pk=profile.pk)

    @staticmethod
    def _get_seller(seller_id):
        try:
            return SellerProfile.objects.get(pk=seller_id)
        except SellerProfile.DoesNotExist as exc:
            raise NotFound("Seller was not found.") from exc

    @staticmethod
    def _ensure_active_seller(seller):
        if (
            not seller.is_active
            or not seller.activated
            or not seller.is_valid
            or getattr(seller, "deleted", None) is not None
        ):
            raise NotFound("Seller was not found.")

    @staticmethod
    def _validate_not_same_user(customer, seller):
        if customer.user_id == seller.user_id:
            raise ValidationError(
                {"seller_id": ("Customers cannot follow their own seller profile.")}
            )

    @staticmethod
    def _handle_follow_reward(follow, customer):
        from apps.loyalty.constants import RewardEventType
        from apps.loyalty.reward_engine import RewardEngine

        RewardEngine.handle_event(
            RewardEventType.SELLER_FOLLOWED,
            actor=customer,
            profile=customer,
            context={"follow": follow},
        )


class SellerPackageFollowService:
    @staticmethod
    def follow(active_profile, seller_package_id):
        customer = SellerFollowService._require_customer(active_profile)
        seller_package = SellerPackageFollowService._get_package(seller_package_id)
        SellerPackageFollowService._validate_followable(customer, seller_package)

        try:
            with transaction.atomic():
                follow, created = SellerPackageFollow.objects.get_or_create(
                    follower_id=customer.pk,
                    seller_package_id=seller_package.pk,
                    defaults={"is_active": True},
                )
                if not created and not follow.is_active:
                    follow.is_active = True
                    follow.save(update_fields=["is_active", "modified"])
                return follow
        except IntegrityError:
            follow = SellerPackageFollow.objects.get(
                follower_id=customer.pk,
                seller_package_id=seller_package.pk,
            )
            if not follow.is_active:
                follow.is_active = True
                follow.save(update_fields=["is_active", "modified"])
            return follow

    @staticmethod
    def unfollow(active_profile, seller_package_id):
        customer = SellerFollowService._require_customer(active_profile)
        seller_package = SellerPackage.objects.filter(pk=seller_package_id).first()
        if seller_package is None:
            raise NotFound("Seller package was not found.")

        SellerPackageFollow.objects.filter(
            follower_id=customer.pk,
            seller_package_id=seller_package.pk,
            is_active=True,
        ).update(is_active=False, modified=timezone.now())
        return seller_package

    @staticmethod
    def following_queryset(active_profile):
        customer = SellerFollowService._require_customer(active_profile)
        blocked_seller_ids = BlockService.get_blocked_seller_ids_for_customer(customer)
        queryset = (
            SellerPackageFollow.objects.filter(
                follower_id=customer.pk,
                is_active=True,
                seller_package__status=SellerPackageStatus.ACTIVE,
                seller_package__activated=True,
                seller_package__is_public=True,
                seller_package__owner__is_active=True,
                seller_package__owner__activated=True,
                seller_package__owner__is_valid=True,
                seller_package__owner__deleted__isnull=True,
            )
            .filter(
                Q(seller_package__expires_at__isnull=True)
                | Q(seller_package__expires_at__gt=timezone.now())
            )
            .select_related("seller_package", "seller_package__owner")
            .order_by("-created", "-id")
        )
        if blocked_seller_ids:
            queryset = queryset.exclude(seller_package__owner_id__in=blocked_seller_ids)
        return queryset

    @staticmethod
    def followed_package_ids(customer_profile):
        return list(
            SellerPackageFollowService.following_queryset(customer_profile).values_list(
                "seller_package_id",
                flat=True,
            )
        )

    @staticmethod
    def _get_package(seller_package_id):
        try:
            return SellerPackage.objects.select_related("owner").get(pk=seller_package_id)
        except SellerPackage.DoesNotExist as exc:
            raise NotFound("Seller package was not found.") from exc

    @staticmethod
    def _validate_followable(customer, seller_package):
        if customer.user_id == seller_package.owner.user_id:
            raise ValidationError(
                {
                    "seller_package_id": (
                        "Customers cannot follow their own seller package."
                    )
                }
            )
        if BlockService.is_blocked_between(customer, seller_package.owner):
            raise ValidationError(
                {
                    "seller_package_id": (
                        "Cannot follow this seller package while a block is active."
                    )
                }
            )
        if (
            seller_package.status != SellerPackageStatus.ACTIVE
            or not seller_package.activated
            or not seller_package.is_public
            or (
                seller_package.expires_at is not None
                and seller_package.expires_at <= timezone.now()
            )
            or not seller_package.owner.is_active
            or not seller_package.owner.activated
            or not seller_package.owner.is_valid
            or getattr(seller_package.owner, "deleted", None) is not None
        ):
            raise ValidationError(
                {"seller_package_id": "Seller package is not followable."}
            )


class FeedCursor:
    @staticmethod
    def parse(raw_cursor):
        if not raw_cursor:
            return {
                "following": None,
                "discovery": None,
                "following_exhausted": False,
            }

        try:
            padded = raw_cursor + "=" * (-len(raw_cursor) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
            data = json.loads(decoded.decode("utf-8"))
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise ValidationError({"cursor": "Invalid cursor."}) from exc

        return {
            "following": FeedCursor._parse_position(data.get("following")),
            "discovery": FeedCursor._parse_position(data.get("discovery")),
            "following_exhausted": bool(data.get("following_exhausted")),
        }

    @staticmethod
    def encode(state):
        data = {
            "following": FeedCursor._dump_position(state.get("following")),
            "discovery": FeedCursor._dump_position(state.get("discovery")),
            "following_exhausted": bool(state.get("following_exhausted")),
        }
        payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def from_object(obj):
        return {
            "created_at": obj.created.isoformat(),
            "id": obj.pk,
        }

    @staticmethod
    def _parse_position(value):
        if value in (None, {}):
            return None

        try:
            created_at = parse_datetime(value["created_at"])
            object_id = int(value["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError({"cursor": "Invalid cursor."}) from exc

        if created_at is None:
            raise ValidationError({"cursor": "Invalid cursor."})
        if timezone.is_naive(created_at):
            raise ValidationError({"cursor": "Invalid cursor."})

        return {
            "created_at": created_at,
            "id": object_id,
        }

    @staticmethod
    def _dump_position(value):
        if not value:
            return None

        created_at = value["created_at"]
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        return {
            "created_at": created_at,
            "id": value["id"],
        }


class FeedService:
    @staticmethod
    def parse_limit(raw_limit):
        if raw_limit in (None, ""):
            return DEFAULT_FEED_LIMIT
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"limit": "Limit must be an integer."}) from exc
        if limit < 1:
            raise ValidationError({"limit": "Limit must be at least 1."})
        return min(limit, MAX_FEED_LIMIT)

    @staticmethod
    def parse_suggestion_limit(raw_limit):
        if raw_limit in (None, ""):
            return DEFAULT_SELLER_SUGGESTION_LIMIT
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"limit": "Limit must be an integer."}) from exc
        if limit < 1:
            raise ValidationError({"limit": "Limit must be at least 1."})
        return min(limit, MAX_FEED_LIMIT)

    @staticmethod
    def get_people_you_may_know(customer_profile, limit):
        customer = SellerFollowService._require_customer(customer_profile)
        followed_seller_ids = SellerFollowService.followed_seller_ids(customer)
        blocked_seller_ids = BlockService.get_blocked_seller_ids_for_customer(customer)

        queryset = SellerProfile.objects.filter(
            is_active=True,
            activated=True,
            is_valid=True,
            deleted__isnull=True,
        ).exclude(user_id=customer.user_id)
        queryset = queryset.annotate(
            completed_orders_count=Count(
                "sold_order_items",
                filter=Q(
                    sold_order_items__status=OrderItemStatus.COMPLETED,
                    sold_order_items__deleted__isnull=True,
                ),
            )
        )
        if followed_seller_ids:
            queryset = queryset.exclude(pk__in=followed_seller_ids)
        if blocked_seller_ids:
            queryset = queryset.exclude(pk__in=blocked_seller_ids)

        return list(queryset.order_by("-completed_orders_count", Random())[:limit])

    @staticmethod
    def get_auction_feed(customer_profile, limit, cursor=None):
        customer = SellerFollowService._require_customer(customer_profile)
        followed_seller_ids = SellerFollowService.followed_seller_ids(customer)
        followed_package_ids = SellerPackageFollowService.followed_package_ids(customer)
        blocked_seller_ids = BlockService.get_blocked_seller_ids_for_customer(customer)
        cursor_state = FeedCursor.parse(cursor)

        now = timezone.now()
        base_queryset = (
            Auction.objects.filter(
                deleted__isnull=True,
                owner__is_active=True,
                owner__activated=True,
                owner__is_valid=True,
                owner__deleted__isnull=True,
                status=AuctionStatus.ACTIVE,
                is_visible=True,
                start_time__lte=now,
                end_time__gt=now,
            )
            .select_related("owner", "owner__sellerprofile")
            .prefetch_related("items")
            .order_by("-created", "-id")
        )
        if blocked_seller_ids:
            base_queryset = base_queryset.exclude(owner_id__in=blocked_seller_ids)

        return FeedService._build_feed(
            base_queryset=base_queryset,
            followed_seller_ids=followed_seller_ids,
            followed_package_ids=followed_package_ids,
            limit=limit,
            cursor_state=cursor_state,
        )

    @staticmethod
    def get_market_feed(customer_profile, limit, cursor=None):
        customer = SellerFollowService._require_customer(customer_profile)
        followed_seller_ids = SellerFollowService.followed_seller_ids(customer)
        followed_package_ids = SellerPackageFollowService.followed_package_ids(customer)
        blocked_seller_ids = BlockService.get_blocked_seller_ids_for_customer(customer)
        cursor_state = FeedCursor.parse(cursor)

        base_queryset = (
            Asset.objects.filter(
                deleted__isnull=True,
                owner__is_active=True,
                owner__activated=True,
                owner__is_valid=True,
                owner__deleted__isnull=True,
                category__in=PRODUCT_MARKET_CATEGORIES,
                is_market_listed=True,
                status=AssetStatus.AVAILABLE,
                count__gt=0,
            )
            .select_related("owner", "owner__sellerprofile")
            .prefetch_related("media")
            .order_by("-created", "-id")
        )
        if blocked_seller_ids:
            base_queryset = base_queryset.exclude(owner_id__in=blocked_seller_ids)

        return FeedService._build_feed(
            base_queryset=base_queryset,
            followed_seller_ids=followed_seller_ids,
            followed_package_ids=followed_package_ids,
            limit=limit,
            cursor_state=cursor_state,
        )

    @staticmethod
    def _build_feed(
        *,
        base_queryset,
        followed_seller_ids,
        followed_package_ids,
        limit,
        cursor_state,
    ):
        results = []
        returned_ids = set()
        next_state = {
            "following": cursor_state.get("following"),
            "discovery": cursor_state.get("discovery"),
            "following_exhausted": cursor_state.get("following_exhausted")
            or not (followed_seller_ids or followed_package_ids),
        }
        following_has_more = False
        discovery_has_more = False

        if (followed_seller_ids or followed_package_ids) and not next_state["following_exhausted"]:
            following_filter = Q()
            if followed_seller_ids:
                following_filter |= Q(owner_id__in=followed_seller_ids)
            if followed_package_ids:
                following_filter |= Q(seller_package_id__in=followed_package_ids)
            following_queryset = base_queryset.filter(following_filter)
            following_items, following_has_more = FeedService._page_queryset(
                following_queryset,
                limit,
                cursor_state.get("following"),
            )
            for item in following_items:
                results.append(
                    {
                        "source": FeedService._following_source(
                            item,
                            followed_seller_ids,
                            followed_package_ids,
                        ),
                        "item": item,
                    }
                )
                returned_ids.add(item.pk)
            if following_items:
                next_state["following"] = FeedCursor.from_object(following_items[-1])
            if not following_has_more:
                next_state["following_exhausted"] = True

        remaining = limit - len(results)
        if remaining > 0:
            discovery_queryset = base_queryset
            if followed_seller_ids:
                discovery_queryset = discovery_queryset.exclude(
                    owner_id__in=followed_seller_ids
                )
            if followed_package_ids:
                discovery_queryset = discovery_queryset.exclude(
                    seller_package_id__in=followed_package_ids
                )
            if returned_ids:
                discovery_queryset = discovery_queryset.exclude(pk__in=returned_ids)

            discovery_items, discovery_has_more = FeedService._page_queryset(
                discovery_queryset,
                remaining,
                cursor_state.get("discovery"),
            )
            for item in discovery_items:
                results.append({"source": "discovery", "item": item})
                returned_ids.add(item.pk)
            if discovery_items:
                next_state["discovery"] = FeedCursor.from_object(discovery_items[-1])
        elif next_state["following_exhausted"]:
            discovery_queryset = base_queryset
            if followed_seller_ids:
                discovery_queryset = discovery_queryset.exclude(
                    owner_id__in=followed_seller_ids
                )
            if followed_package_ids:
                discovery_queryset = discovery_queryset.exclude(
                    seller_package_id__in=followed_package_ids
                )
            _, discovery_has_more = FeedService._page_queryset(
                discovery_queryset,
                1,
                cursor_state.get("discovery"),
            )

        has_more = following_has_more or discovery_has_more
        return {
            "results": results,
            "next_cursor": FeedCursor.encode(next_state) if has_more else None,
            "has_more": has_more,
            "meta": {
                "following_count": sum(
                    1
                    for item in results
                    if item["source"] in {"following", "package_following"}
                ),
                "discovery_count": sum(
                    1 for item in results if item["source"] == "discovery"
                ),
                "fallback_used": any(item["source"] == "discovery" for item in results),
                "following_exhausted": next_state["following_exhausted"],
            },
        }

    @staticmethod
    def _page_queryset(queryset, limit, cursor_position):
        if cursor_position:
            queryset = queryset.filter(
                Q(created__lt=cursor_position["created_at"])
                | Q(
                    created=cursor_position["created_at"],
                    id__lt=cursor_position["id"],
                )
            )

        items = list(queryset[: limit + 1])
        return items[:limit], len(items) > limit

    @staticmethod
    def _following_source(item, followed_seller_ids, followed_package_ids):
        if item.owner_id in followed_seller_ids:
            return "following"
        if item.seller_package_id in followed_package_ids:
            return "package_following"
        return "following"

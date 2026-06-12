from copy import deepcopy
from decimal import Decimal

import pytest
from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings


@pytest.mark.django_db(transaction=True)
@override_settings(MIGRATION_MODULES={})
def test_participant_conversation_migrates_to_customer_seller():
    alias = "chat_migration_test"
    database_config = deepcopy(settings.DATABASES["default"])
    database_config["NAME"] = ":memory:"
    database_config["TEST"] = {"NAME": ":memory:"}
    connections.databases[alias] = database_config
    connection = connections[alias]

    try:
        old_target = [("chat", "0001_initial")]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        old_apps = executor.loader.project_state(old_target).apps

        UserAccount = old_apps.get_model("authentication", "UserAccount")
        CustomerProfile = old_apps.get_model("users", "CustomerProfile")
        SellerProfile = old_apps.get_model("users", "SellerProfile")
        Conversation = old_apps.get_model("chat", "Conversation")
        Message = old_apps.get_model("chat", "Message")

        customer_user = UserAccount.objects.using(alias).create(
            username="customer",
            phone_number="+201000000001",
        )
        seller_user = UserAccount.objects.using(alias).create(
            username="seller",
            phone_number="+201000000002",
        )
        customer = CustomerProfile.objects.using(alias).create(
            user=customer_user,
            type="Customer",
            activated=True,
            is_active=True,
            is_valid=True,
            country="EG",
            currency="EGP",
            balance=Decimal("0.00"),
            cashback_balance=Decimal("0.00"),
        )
        seller = SellerProfile.objects.using(alias).create(
            user=seller_user,
            type="Seller",
            activated=True,
            is_active=True,
            is_valid=True,
            country="EG",
            currency="EGP",
            balance=Decimal("0.00"),
        )
        conversation = Conversation.objects.using(alias).create(
            participant_1=customer,
            participant_2=seller,
            created_by=customer,
        )
        message = Message.objects.using(alias).create(
            conversation=conversation,
            sender=customer,
            receiver=seller,
            body="Hello",
        )

        new_target = [("chat", "0002_conversation_customer_seller")]
        executor = MigrationExecutor(connection)
        executor.migrate(new_target)
        new_apps = executor.loader.project_state(new_target).apps

        MigratedConversation = new_apps.get_model("chat", "Conversation")
        MigratedMessage = new_apps.get_model("chat", "Message")
        migrated_conversation = MigratedConversation.objects.using(alias).get(
            pk=conversation.pk
        )
        migrated_message = MigratedMessage.objects.using(alias).get(
            pk=message.pk
        )

        assert migrated_conversation.customer_id == customer.pk
        assert migrated_conversation.seller_id == seller.pk
        assert migrated_message.conversation_id == conversation.pk
        assert migrated_message.sender_id == customer.pk
        assert migrated_message.receiver_id == seller.pk
    finally:
        connection.close()
        connections.databases.pop(alias, None)

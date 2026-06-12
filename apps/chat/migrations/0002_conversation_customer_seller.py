from django.db import migrations, models
import django.db.models.deletion


CUSTOMER_TYPE = "Customer"
SELLER_TYPE = "Seller"


def populate_customer_seller(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Conversation = apps.get_model("chat", "Conversation")
    CustomerProfile = apps.get_model("users", "CustomerProfile")
    SellerProfile = apps.get_model("users", "SellerProfile")

    conversations = Conversation.objects.using(db_alias).select_related(
        "participant_1",
        "participant_2",
    )
    for conversation in conversations:
        participant_1 = conversation.participant_1
        participant_2 = conversation.participant_2
        participant_types = {participant_1.type, participant_2.type}

        if participant_types != {CUSTOMER_TYPE, SELLER_TYPE}:
            raise RuntimeError(
                "Cannot migrate chat conversation "
                f"{conversation.pk}: expected one Customer and one Seller, "
                f"got {participant_1.type} and {participant_2.type}."
            )

        if participant_1.user_id == participant_2.user_id:
            raise RuntimeError(
                "Cannot migrate chat conversation "
                f"{conversation.pk}: customer and seller belong to the same user."
            )

        if participant_1.type == CUSTOMER_TYPE:
            customer = participant_1
            seller = participant_2
        else:
            customer = participant_2
            seller = participant_1

        if not CustomerProfile.objects.using(db_alias).filter(
            pk=customer.pk
        ).exists():
            raise RuntimeError(
                "Cannot migrate chat conversation "
                f"{conversation.pk}: customer profile {customer.pk} has no "
                "CustomerProfile row."
            )
        if not SellerProfile.objects.using(db_alias).filter(
            pk=seller.pk
        ).exists():
            raise RuntimeError(
                "Cannot migrate chat conversation "
                f"{conversation.pk}: seller profile {seller.pk} has no "
                "SellerProfile row."
            )

        Conversation.objects.using(db_alias).filter(pk=conversation.pk).update(
            customer_id=customer.pk,
            seller_id=seller.pk,
        )


def reverse_populate_participants(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Conversation = apps.get_model("chat", "Conversation")

    for conversation in Conversation.objects.using(db_alias).all():
        participant_ids = sorted(
            [conversation.customer_id, conversation.seller_id]
        )
        Conversation.objects.using(db_alias).filter(pk=conversation.pk).update(
            participant_1_id=participant_ids[0],
            participant_2_id=participant_ids[1],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0005_customerprofile_cashback_balance"),
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="customer",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="chat_conversations_as_customer",
                to="users.customerprofile",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="seller",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="chat_conversations_as_seller",
                to="users.sellerprofile",
            ),
        ),
        migrations.RunPython(
            populate_customer_seller,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="conversation",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="chat_conversations_as_customer",
                to="users.customerprofile",
            ),
        ),
        migrations.AlterField(
            model_name="conversation",
            name="seller",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="chat_conversations_as_seller",
                to="users.sellerprofile",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="conversation",
            name="chat_conversation_distinct_participants",
        ),
        migrations.RemoveConstraint(
            model_name="conversation",
            name="chat_unique_conversation_participant_pair",
        ),
        migrations.RemoveIndex(
            model_name="conversation",
            name="chat_conver_partici_96ea2e_idx",
        ),
        migrations.RemoveIndex(
            model_name="conversation",
            name="chat_conver_partici_4fdfd2_idx",
        ),
        migrations.RemoveIndex(
            model_name="conversation",
            name="chat_conver_last_me_f07e7c_idx",
        ),
        migrations.AlterField(
            model_name="conversation",
            name="participant_1",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="conversations_as_participant_1",
                to="users.profile",
            ),
        ),
        migrations.AlterField(
            model_name="conversation",
            name="participant_2",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="conversations_as_participant_2",
                to="users.profile",
            ),
        ),
        migrations.RunPython(
            migrations.RunPython.noop,
            reverse_populate_participants,
        ),
        migrations.RemoveField(
            model_name="conversation",
            name="participant_1",
        ),
        migrations.RemoveField(
            model_name="conversation",
            name="participant_2",
        ),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(
                fields=["customer", "last_message_at"],
                name="chat_conv_customer_last_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(
                fields=["seller", "last_message_at"],
                name="chat_conv_seller_last_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(
                fields=["last_message_at"],
                name="chat_conv_last_msg_at_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="conversation",
            constraint=models.UniqueConstraint(
                fields=("customer", "seller"),
                name="chat_unique_conversation_customer_seller",
            ),
        ),
    ]

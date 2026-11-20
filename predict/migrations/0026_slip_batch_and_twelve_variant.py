from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("predict", "0025_add_match_date_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="SlipBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("variant", models.CharField(choices=[("3", "Running Bet"), ("4", "Mshipi")], max_length=1)),
                ("status", models.CharField(choices=[("ACTIVE", "Active"), ("LOST", "Lost"), ("WON", "Won")], db_index=True, default="ACTIVE", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AlterField(
            model_name="toppick",
            name="variant",
            field=models.CharField(choices=[("1", "Sure 1"), ("2", "Sure 2"), ("3", "Running Bet"), ("4", "Mshipi"), ("5", "12")], default="1", max_length=1),
        ),
        migrations.AddField(
            model_name="toppick",
            name="slip_batch",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="picks", to="predict.slipbatch"),
        ),
    ]

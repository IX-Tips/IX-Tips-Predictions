from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("predict", "0026_slip_batch_and_twelve_variant"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="toppick",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="toppick",
            constraint=models.UniqueConstraint(
                fields=("match_date", "home_team", "away_team", "variant"),
                condition=models.Q(variant__in=["1", "2", "5"]),
                name="unique_regular_top_pick_fixture_variant",
            ),
        ),
        migrations.AddConstraint(
            model_name="toppick",
            constraint=models.UniqueConstraint(
                fields=("match_date", "home_team", "away_team", "variant", "slip_batch"),
                condition=models.Q(variant__in=["3", "4"]),
                name="unique_batched_top_pick_fixture_variant",
            ),
        ),
    ]

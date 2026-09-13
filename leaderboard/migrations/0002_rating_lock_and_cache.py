from django.db import migrations


def seed_lock(apps, schema_editor):
    apps.get_model("leaderboard", "RatingState").objects.using(
        schema_editor.connection.alias,
    ).get_or_create(pk=1)


def remove_lock(apps, schema_editor):
    apps.get_model("leaderboard", "RatingState").objects.using(
        schema_editor.connection.alias,
    ).filter(pk=1).delete()


class Migration(migrations.Migration):
    dependencies = [("leaderboard", "0001_initial")]

    operations = [
        migrations.RunPython(seed_lock, remove_lock),
        migrations.RunSQL(
            """
            CREATE TABLE rate_limit_cache (
                cache_key varchar(255) PRIMARY KEY,
                value text NOT NULL,
                expires timestamptz NOT NULL
            );
            CREATE INDEX rate_limit_cache_expires ON rate_limit_cache (expires);
            """,
            "DROP TABLE rate_limit_cache;",
        ),
    ]

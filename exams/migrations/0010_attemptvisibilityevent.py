import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("exams", "0009_gradeappeal_student_note")]

    operations = [
        migrations.CreateModel(
            name="AttemptVisibilityEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.UUIDField()),
                ("left_at", models.DateTimeField(auto_now_add=True, verbose_name="Страница скрыта")),
                ("returned_at", models.DateTimeField(blank=True, null=True, verbose_name="Вернулся на страницу")),
                ("attempt", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                related_name="visibility_events", to="exams.attempt")),
            ],
            options={
                "verbose_name": "Уход со страницы экзамена",
                "verbose_name_plural": "Уходы со страницы экзамена",
                "ordering": ["left_at"],
                "constraints": [models.UniqueConstraint(fields=("attempt", "event_id"),
                                                       name="unique_attempt_visibility_event")],
            },
        ),
    ]

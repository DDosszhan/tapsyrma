import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0014_question_interaction_types"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RegradeJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Queued"), ("grading", "Regrading"),
                                                      ("done", "Completed"), ("error", "Failed")],
                                            default="pending", max_length=12)),
                ("original_status", models.CharField(choices=[("active", "In progress"),
                                                               ("pending", "Awaiting grading"),
                                                               ("grading", "Being graded"),
                                                               ("done", "Graded"),
                                                               ("error", "Manual review required")], max_length=12)),
                ("original_grades", models.JSONField(default=dict)),
                ("original_score", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("candidate_grades", models.JSONField(default=dict)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("attempt", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                              related_name="regrade_jobs", to="exams.attempt")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                   to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"],
                     "indexes": [models.Index(fields=["status", "created_at"], name="regrade_status_idx")]},
        ),
    ]

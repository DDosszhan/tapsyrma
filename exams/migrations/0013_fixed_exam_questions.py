from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0012_employee_grade"),
    ]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="answers_released",
            field=models.BooleanField(default=False, verbose_name="Show answers and grading details to students"),
        ),
        migrations.AddField(
            model_name="exam",
            name="mode",
            field=models.CharField(choices=[("generated", "Different generated questions for each student"),
                                            ("fixed", "Same teacher-provided questions for everyone")],
                                   default="generated", max_length=12, verbose_name="Question source"),
        ),
        migrations.AddField(
            model_name="question",
            name="grading_mode",
            field=models.CharField(choices=[("ai", "AI grading"), ("manual", "Teacher grading")],
                                   default="ai", max_length=8, verbose_name="Grading"),
        ),
        migrations.AddField(
            model_name="question",
            name="image",
            field=models.FileField(blank=True, upload_to="exam_question_images/%Y/%m/", verbose_name="Question image"),
        ),
        migrations.AddField(
            model_name="question",
            name="requires_image",
            field=models.BooleanField(default=False, verbose_name="Image required for this question"),
        ),
        migrations.AlterField(
            model_name="question",
            name="subtopic",
            field=models.CharField(blank=True, max_length=200, verbose_name="Subtopic"),
        ),
        migrations.AlterField(
            model_name="question",
            name="rubric",
            field=models.JSONField(blank=True, default=list, verbose_name="Criteria"),
        ),
        migrations.AlterField(
            model_name="question",
            name="model_answer",
            field=models.TextField(blank=True, verbose_name="Model answer"),
        ),
        migrations.AlterField(
            model_name="question",
            name="subtopic_explanation",
            field=models.TextField(blank=True, verbose_name="Subtopic explanation"),
        ),
        migrations.AlterField(
            model_name="question",
            name="answer_explanation",
            field=models.TextField(blank=True, verbose_name="Answer explanation"),
        ),
    ]

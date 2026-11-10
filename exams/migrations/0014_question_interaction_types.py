from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0013_fixed_exam_questions"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="interaction",
            field=models.JSONField(blank=True, default=dict, verbose_name="Structured answer key"),
        ),
        migrations.AlterField(
            model_name="question",
            name="kind",
            field=models.CharField(choices=[
                ("mcq", "MCQ — one correct answer"),
                ("open", "Open response"),
                ("cloze", "Fill in the blanks"),
                ("matching", "Matching"),
                ("multiselect", "Select several answers"),
                ("ordering", "Put in order"),
            ], max_length=20, verbose_name="Type"),
        ),
    ]

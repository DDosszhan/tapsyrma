from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("courses", "0004_lessonanswer_criteria_scores")]

    operations = [
        migrations.AddField(
            model_name="lessonquestion",
            name="student_guidance",
            field=models.TextField(
                blank=True,
                help_text="Shown only after submission. Write one answer-free hint per marking criterion, in the same order.",
                verbose_name="Student marking guidance",
            ),
        ),
    ]

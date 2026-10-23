from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("courses", "0003_alter_course_options_alter_lesson_options_and_more")]

    operations = [
        migrations.AlterField(
            model_name="lessonquestion",
            name="grading_criterion",
            field=models.TextField(
                blank=True,
                help_text="For itemized marks, write one line per criterion, for example: 1: Correct method",
                verbose_name="Marking criterion",
            ),
        ),
        migrations.AddField(
            model_name="lessonanswer",
            name="criteria_scores",
            field=models.JSONField(blank=True, default=list, verbose_name="Marks by criterion"),
        ),
    ]

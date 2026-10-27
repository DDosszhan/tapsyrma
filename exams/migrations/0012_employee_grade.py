from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0011_alter_attempt_options_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="exam",
            name="grade",
            field=models.PositiveSmallIntegerField(
                "Grade", choices=[(9, "Grade 9"), (10, "Grade 10"), (0, "Сотрудник")],
            ),
        ),
        migrations.AlterField(
            model_name="student",
            name="grade",
            field=models.PositiveSmallIntegerField(
                "Grade", choices=[(9, "Grade 9"), (10, "Grade 10"), (0, "Сотрудник")],
            ),
        ),
        migrations.RemoveConstraint(
            model_name="exam",
            name="exam_grade_9_or_10",
        ),
        migrations.AddConstraint(
            model_name="exam",
            constraint=models.CheckConstraint(
                condition=models.Q(grade__in=[0, 9, 10]), name="exam_grade_0_9_or_10",
            ),
        ),
    ]

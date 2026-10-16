from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("exams", "0008_gradeappeal")]

    operations = [
        migrations.AddField(
            model_name="gradeappeal",
            name="student_note",
            field=models.TextField(blank=True, verbose_name="Пояснение ученика"),
        ),
    ]

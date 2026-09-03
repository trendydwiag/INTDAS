from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0002_add_details_kbli_codes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="companyprofile",
            name="npwp",
            field=models.CharField(blank=True, db_index=True, default="", max_length=20),
        ),
    ]

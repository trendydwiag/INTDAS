from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("web", "0008_tenderresult_jadwal_json_and_more"),
        ("submissions", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenderresult",
            name="ai_score",
            field=models.IntegerField(default=0, db_index=True, help_text="Persisted Tingkat Kecocokan score (0-100)"),
        ),
        migrations.AddField(
            model_name="tenderresult",
            name="ai_analysis_json",
            field=models.JSONField(default=dict, blank=True, help_text="Full AI analysis: {fit_score, summary, criteria, llm_provider}"),
        ),
    ]

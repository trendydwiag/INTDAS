from django.db import models


class AIMatchResult(models.Model):
    """Stores AI qualification match results between a tender and a company."""

    tender = models.ForeignKey(
        "web.TenderResult",
        on_delete=models.CASCADE,
        related_name="ai_matches",
    )
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.CASCADE,
        related_name="ai_matches",
    )
    ELIGIBILITY_CHOICES = [
        ("ELIGIBLE", "Eligible"),
        ("CONDITIONALLY_ELIGIBLE", "Conditionally Eligible"),
        ("NOT_ELIGIBLE", "Not Eligible"),
        ("NOT_READY", "Not Ready"),
        ("NOT_APPLICABLE", "Not Applicable"),
    ]

    fit_score = models.IntegerField(default=0, help_text="0-100 percentage fit score")
    eligibility_status = models.CharField(
        max_length=30,
        choices=ELIGIBILITY_CHOICES,
        default="ELIGIBLE",
        db_index=True,
        help_text="Hard qualification eligibility status",
    )
    mandatory_passed = models.BooleanField(
        default=True,
        help_text="True if all hard/mandatory requirements passed",
    )
    blockers = models.JSONField(
        default=list,
        blank=True,
        help_text="List of mandatory requirement failures or blockers",
    )
    missing_requirements = models.JSONField(
        default=list,
        blank=True,
        help_text="List of requirements needing evidence or action",
    )
    recommended_actions = models.JSONField(
        default=list,
        blank=True,
        help_text="Suggested concrete next steps for company",
    )
    matcher_version = models.CharField(
        max_length=20,
        default="v0.2",
        help_text="Matcher engine version",
    )
    summary = models.TextField(default="")
    criteria_json = models.JSONField(default=list, help_text="Array of {requirement, status, evidence, action}")
    llm_provider = models.CharField(max_length=20, default="")
    llm_model = models.CharField(max_length=50, default="")
    token_input = models.IntegerField(default=0)
    token_output = models.IntegerField(default=0)
    is_fallback = models.BooleanField(
        default=False,
        help_text="True if generated via fallback engine due to provider error or missing key",
    )
    fallback_reason = models.CharField(max_length=255, default="", blank=True)
    cache_key = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("tender", "company")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["cache_key"]),
        ]

    def __str__(self) -> str:
        return f"Match: Tender#{self.tender_id} x Company#{self.company_id} = {self.fit_score}%"

from django.conf import settings
from django.db import models


class TenderWatchlist(models.Model):
    """Saves a tender for monitoring by a company/user."""

    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.CASCADE,
        related_name="watchlist",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="watchlist",
    )
    tender = models.ForeignKey(
        "web.TenderResult",
        on_delete=models.CASCADE,
        related_name="watchlisted_by",
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("company", "tender")
        ordering = ["-created_at"]
        verbose_name = "Tender Watchlist"
        verbose_name_plural = "Tender Watchlists"

    def __str__(self):
        return f"Watchlist: Tender#{self.tender_id} by Company#{self.company_id}"

from django.db import models
from django.conf import settings


class TenderSubmissionStatus(models.Model):
    """Tracks submission status for a tender by a specific company."""

    STATUS_CHOICES = [
        ("belum_diproses", "Belum Diproses"),
        ("cocok_diproses", "Cocok & Diproses"),
        ("sudah_submit", "Sudah Submit"),
        ("menang", "Berhasil Menang"),
        ("gagal", "Gagal Menang"),
        ("tidak_cocok", "Tidak Cocok"),
    ]

    tender = models.ForeignKey(
        "web.TenderResult",
        on_delete=models.CASCADE,
        related_name="submission_statuses",
    )
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.CASCADE,
        related_name="submission_statuses",
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="belum_diproses")
    notes = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="status_updates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tender", "company")
        ordering = ["-updated_at"]
        verbose_name = "Submission Status"
        verbose_name_plural = "Submission Statuses"

    def __str__(self) -> str:
        return f"Tender#{self.tender_id} x Company#{self.company_id}: {self.get_status_display()}"

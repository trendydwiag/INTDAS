from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom User model — extends Django AbstractUser with role and company fields."""

    ROLE_CHOICES = [
        ("superadmin", "Superadmin"),
        ("company_admin", "Admin Perusahaan"),
        ("submitter", "Admin Submitter"),
    ]

    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="submitter")
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self) -> str:
        return f"{self.get_full_name() or self.username} ({self.role})"

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

    @property
    def is_company_admin(self) -> bool:
        return self.role == "company_admin"

    @property
    def is_submitter(self) -> bool:
        return self.role == "submitter"

    @property
    def company_name(self) -> str:
        return self.company.name if self.company else ""

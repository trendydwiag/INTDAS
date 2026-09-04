from django.db import models


class CompanyProfile(models.Model):
    """Company profile — represents a business entity in the multi-tenant system."""

    name = models.CharField(max_length=200)
    nib = models.CharField("NIB (Nomor Induk Berusaha)", max_length=30, unique=True)
    npwp = models.CharField(max_length=20, blank=True, default="", db_index=True)
    address = models.TextField(blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    contact_person = models.CharField(max_length=100, blank=True, default="")
    modal_disetor = models.BigIntegerField(default=0, help_text="Modal disetor dalam Rupiah")
    penghasilan_tahunan = models.BigIntegerField(default=0, help_text="Penghasilan brutro tahunan dalam Rupiah")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.nib})"

    @property
    def kbli_codes_from_quals(self) -> set:
        """Return all unique KBLI codes from active qualifications."""
        codes = set()
        for q in self.qualifications.filter(status="active"):
            for code in (q.kbli_codes or []):
                if code:
                    codes.add(str(code).strip())
            if q.kbli_code:
                codes.add(q.kbli_code.strip())
        return codes


class CompanyQualification(models.Model):
    """Qualification data for a company — Izin Usaha, SBU, Pengalaman Kerja, etc."""

    CATEGORY_CHOICES = [
        ("izin_usaha", "Izin Usaha"),
        ("sbu", "SBU (Sertifikat Badan Usaha)"),
        ("pengalaman_kerja", "Pengalaman Kerja"),
        ("sertifikasi", "Sertifikasi"),
        ("sdm", "Sumber Daya Manusia"),
    ]

    KLASIFIKASI_CHOICES = [
        ("kecil", "Kecil"),
        ("menengah", "Menengah"),
        ("besar", "Besar"),
    ]

    STATUS_CHOICES = [
        ("active", "Aktif"),
        ("expired", "Kedaluwarsa"),
        ("needs_renewal", "Perlu Diperbarui"),
    ]

    company = models.ForeignKey(CompanyProfile, on_delete=models.CASCADE, related_name="qualifications")
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    name = models.CharField(max_length=200, help_text="Nama izin / sertifikat / proyek")
    number = models.CharField(max_length=100, blank=True, default="", help_text="Nomor izin / sertifikat")
    kbli_code = models.CharField(max_length=10, blank=True, default="", help_text="Kode KBLI (legacy, single)")
    kbli_codes = models.JSONField(default=list, blank=True, help_text="Multiple KBLI codes, e.g. [\"62019\",\"62090\"]")
    details = models.JSONField(default=dict, blank=True, help_text="Category-specific attributes (dynamic fields)")
    klasifikasi_usaha = models.CharField(max_length=20, choices=KLASIFIKASI_CHOICES, blank=True, default="")
    value_amount = models.BigIntegerField(default=0, help_text="Nilai HPS max / modal / kontrak (Rp)")
    project_name = models.CharField(max_length=300, blank=True, default="", help_text="Nama proyek (untuk pengalaman kerja)")
    client_name = models.CharField(max_length=200, blank=True, default="", help_text="Nama klien (untuk pengalaman kerja)")
    project_year = models.IntegerField(default=0, help_text="Tahun proyek (untuk pengalaman kerja)")
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "name"]
        indexes = [
            models.Index(fields=["company", "category"]),
        ]

    def __str__(self) -> str:
        return f"[{self.category}] {self.name} — {self.company.name}"

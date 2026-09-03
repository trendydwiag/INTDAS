from django.db import models


class KbliMaster(models.Model):
    """KBLI code reference table — drives crawler matching & dashboard filters."""

    code = models.CharField(max_length=10, primary_key=True, unique=True)
    name = models.CharField(max_length=300, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} — {self.name[:50]}"


class TenderResult(models.Model):
    """Stores scraped tender data from SPSE Inaproc."""

    kode_instansi = models.CharField(max_length=50, db_index=True)
    id_lelang = models.CharField(max_length=100, db_index=True)
    nama_paket = models.TextField(default="")
    instansi = models.TextField(default="")
    satuan_kerja = models.CharField(max_length=300, default="", blank=True)
    hps = models.BigIntegerField(default=0)
    jenis_pengadaan = models.CharField(max_length=200, default="", db_index=True)
    metode_pengadaan = models.CharField(max_length=200, default="", blank=True)
    tahap_saat_ini = models.CharField(max_length=200, default="", db_index=True)
    is_prakualifikasi = models.BooleanField(default=False, db_index=True)
    kbli_code = models.CharField(max_length=10, default="", blank=True, db_index=True)
    kbli_description = models.CharField(max_length=300, default="", blank=True)
    is_it_priority = models.BooleanField(default=False, db_index=True)
    priority_score = models.IntegerField(default=0, db_index=True)
    url_pengumuman = models.URLField(default="")
    requirement_text = models.TextField(default="", blank=True, help_text="Requirement text from SPSE pengumuman page, stored during crawl")
    tanggal_dibuat = models.DateTimeField(null=True, blank=True, db_index=True)
    scraped_at = models.DateTimeField(auto_now_add=True)

    # Detail page enrichment fields
    jadwal_json = models.JSONField(default=list, blank=True, help_text="Array of jadwal tahapan: [{no, tahap, mulai, sampai, perubahan}]")
    syarat_kualifikasi = models.TextField(default="", blank=True, help_text="Cleaned qualification requirement text from detail page")
    peserta_count = models.IntegerField(default=0, db_index=True, help_text="Number of participants from peserta page")
    lokasi_pekerjaan = models.CharField(max_length=500, default="", blank=True, help_text="Work location from detail page")
    tahun_anggaran = models.CharField(max_length=20, default="", blank=True, help_text="Budget year, e.g. '2026'")
    satuan_kerja_detail = models.CharField(max_length=300, default="", blank=True, help_text="Satuan kerja from detail page")

    # AI analysis persistence
    ai_score = models.IntegerField(default=0, db_index=True, help_text="Persisted Tingkat Kecocokan score (0-100)")
    ai_analysis_json = models.JSONField(default=dict, blank=True, help_text="Full AI analysis: {fit_score, summary, criteria, llm_provider}")

    class Meta:
        unique_together = ("kode_instansi", "id_lelang")
        ordering = ["-scraped_at"]

    def __str__(self) -> str:
        return f"[{self.kode_instansi}] {self.id_lelang} — {self.nama_paket[:60]}"


class TenderParticipant(models.Model):
    """A company that submitted a bid / participated in a specific tender.

    Populated from the SPSE ``/peserta`` page (columns "No, Nama, NPWP, ...").
    Source identity (``name``/``npwp``) is preserved as-is. A ``company`` FK is
    populated ONLY on a conservative EXACT identity match (normalized NPWP or a
    verified official identifier) — never on name similarity alone (Phase 6C).
    """

    tender = models.ForeignKey(
        TenderResult,
        on_delete=models.CASCADE,
        related_name="participants",
        help_text="Tender this company participated in.",
    )
    name = models.CharField(max_length=300, db_index=True, help_text="Participant company name from SPSE peserta page.")
    npwp = models.CharField(max_length=50, default="", blank=True, help_text="External NPWP identifier from SPSE peserta page.")
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="participations",
        help_text="Resolved CompanyProfile for this participant (EXACT match only; never inferred).",
    )
    resolution_status = models.CharField(
        max_length=40,
        default="",
        blank=True,
        help_text="Entity-resolution match status (EXACT_NPWP / EXACT_OFFICIAL_IDENTIFIER / CANDIDATE_ONLY / IDENTITY_CONFLICT / UNRESOLVED).",
    )
    source_url = models.URLField(default="", blank=True, help_text="SPSE page the participant list was read from.")
    source_type = models.CharField(max_length=50, default="peserta_page", help_text="Provenance: which SPSE source produced this record.")
    source_fetched_at = models.DateTimeField(null=True, blank=True, help_text="When the source page was fetched.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tender", "name"],
                name="uniq_tender_participant_name",
            )
        ]
        indexes = [
            models.Index(fields=["tender"]),
            models.Index(fields=["npwp"]),
            models.Index(fields=["company"]),
        ]

    def __str__(self) -> str:
        return f"[{self.tender_id}] {self.name}"


class TenderWinner(models.Model):
    """The awarded winner of a completed tender.

    Populated from the real SPSE ``/evaluasi/{id_lelang}/pemenang`` page, which
    was verified during Phase 6B source reconnaissance (real captured HTML, not
    inferred). The page exposes:
      - Nama Pemenang, Alamat, NPWP (may be masked by SPSE)
      - Harga Penawaran, Harga Terkoreksi, Harga Negosiasi
    The source is an external identity only. A ``company`` FK is populated ONLY
    on a conservative EXACT identity match (normalized NPWP / verified official
    identifier); winner identity is never inferred from participant ordering.
    """

    tender = models.OneToOneField(
        TenderResult,
        on_delete=models.CASCADE,
        related_name="winner",
        help_text="Tender this winner was awarded for.",
    )
    company_name = models.CharField(max_length=300, default="", blank=True, help_text="Winner company name from the SPSE pemenang page (external identity only, not auto-linked).")
    npwp = models.CharField(max_length=50, default="", blank=True, help_text="Winner NPWP from SPSE (may be masked).")
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wins",
        help_text="Resolved CompanyProfile for this winner (EXACT match only; never inferred from participant ordering).",
    )
    resolution_status = models.CharField(
        max_length=40,
        default="",
        blank=True,
        help_text="Entity-resolution match status (EXACT_NPWP / EXACT_OFFICIAL_IDENTIFIER / CANDIDATE_ONLY / IDENTITY_CONFLICT / UNRESOLVED).",
    )
    alamat = models.TextField(default="", blank=True, help_text="Winner company address from SPSE pemenang page.")
    winning_value = models.BigIntegerField(null=True, blank=True, help_text="Final agreed value in IDR = Harga Negosiasi (the negotiated price). See harga_* fields for the full price breakdown.")
    harga_penawaran = models.BigIntegerField(null=True, blank=True, help_text="Harga Penawaran (offer price) in IDR, from SPSE pemenang page.")
    harga_terkoreksi = models.BigIntegerField(null=True, blank=True, help_text="Harga Terkoreksi (corrected price) in IDR, from SPSE pemenang page.")
    harga_negosiasi = models.BigIntegerField(null=True, blank=True, help_text="Harga Negosiasi (negotiated/final price) in IDR, from SPSE pemenang page.")
    source_url = models.URLField(default="", blank=True, help_text="SPSE page the winner was read from (/evaluasi/{id}/pemenang).")
    source_type = models.CharField(max_length=50, default="pemenang_page", help_text="Provenance: which SPSE source produced this record.")
    source_fetched_at = models.DateTimeField(null=True, blank=True, help_text="When the source page was fetched.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company"]),
        ]

    def __str__(self) -> str:
        return f"[{self.tender_id}] {self.company_name}"


class CrawlJob(models.Model):
    """Tracks crawl job execution status."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    instansi_input = models.TextField(help_text="Comma-separated instansi codes or 'all'")
    workers = models.IntegerField(default=3)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    total_packages = models.IntegerField(default=0)
    total_details = models.IntegerField(default=0)
    error_message = models.TextField(default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"CrawlJob #{self.id} — {self.status} ({self.instansi_input})"


class OpportunityScore(models.Model):
    """Persisted deterministic opportunity score (v0.1).

    Calculates how worthwhile a tender is for a company to pursue,
    based on qualification fit, financial capacity, experience,
    deadline feasibility, and strategic alignment.

    Requires AIMatchResult to exist — returns NOT_READY state otherwise.
    """

    STATUS_CHOICES = [
        ("ready", "Ready"),
        ("not_ready", "Not Ready"),
        ("stale", "Stale"),
    ]

    CLASSIFICATION_CHOICES = [
        ("PRIORITAS_TINGGI", "Prioritas Tinggi"),
        ("LAYAK_DIKEJAR", "Layak Dikejar"),
        ("REVIEW", "Review"),
        ("RISIKO_TINGGI", "Risiko Tinggi"),
        ("TIDAK_DIREKOMENDASIKAN", "Tidak Direkomendasikan"),
    ]

    tender = models.ForeignKey(
        "web.TenderResult",
        on_delete=models.CASCADE,
        related_name="opportunity_scores",
    )
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.CASCADE,
        related_name="opportunity_scores",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="not_ready",
        db_index=True,
    )
    qualification_score = models.IntegerField(default=0, help_text="0-100")
    financial_score = models.IntegerField(default=0, help_text="0-100")
    experience_score = models.IntegerField(default=0, help_text="0-100")
    deadline_score = models.IntegerField(default=0, help_text="0-100")
    strategic_score = models.IntegerField(default=0, help_text="0-100")
    final_score = models.IntegerField(default=0, help_text="0-100, weighted total")
    classification = models.CharField(
        max_length=30,
        choices=CLASSIFICATION_CHOICES,
        default="TIDAK_DIREKOMENDASIKAN",
    )
    breakdown_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Explainability data: {components, explanation}",
    )
    calculation_version = models.CharField(
        max_length=20,
        default="v0.1",
        help_text="Scoring formula version used",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tender", "company")
        ordering = ["-final_score"]
        indexes = [
            models.Index(fields=["company", "final_score"]),
            models.Index(fields=["company", "classification"]),
        ]

    def __str__(self) -> str:
        return (
            f"Score: Tender#{self.tender_id} x Company#{self.company_id}"
            f" = {self.final_score} ({self.classification})"
        )


class IntelligenceJob(models.Model):
    """Persistent job queue for the automated intelligence pipeline.

    Drives the per-(tender, company) flow: AI Match -> Opportunity Score.
    One job records the pipeline stage for a (tender, company) pair.
    Bounded, idempotent via unique (tender, company, job_type).
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    ]

    JOB_TYPE_CHOICES = [
        ("AI_MATCH", "AI Match"),
        ("OPPORTUNITY_SCORE", "Opportunity Score"),
    ]

    tender = models.ForeignKey(
        "web.TenderResult",
        on_delete=models.CASCADE,
        related_name="intelligence_jobs",
    )
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.CASCADE,
        related_name="intelligence_jobs",
    )
    job_type = models.CharField(max_length=20, choices=JOB_TYPE_CHOICES)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    attempts = models.IntegerField(default=0, help_text="Number of processed attempts")
    max_attempts = models.IntegerField(default=3)
    priority = models.IntegerField(default=0)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tender", "company", "job_type")
        ordering = ["-priority", "scheduled_at", "id"]
        indexes = [
            models.Index(fields=["status", "scheduled_at"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["tender", "company"]),
        ]

    def __str__(self) -> str:
        return (
            f"IntelligenceJob: {self.job_type} "
            f"Tender#{self.tender_id} x Company#{self.company_id} — {self.status}"
        )


class IntelligenceDailyUsage(models.Model):
    """Per-company daily budget counter for AI Match executions.

    Counts actual LLM/provider executions (not cache hits, not queued jobs),
    enforcing the AI_MAX_MATCHES_PER_DAY budget.
    """

    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.CASCADE,
        related_name="intelligence_daily_usage",
    )
    usage_date = models.DateField(db_index=True)
    ai_matches = models.IntegerField(default=0, help_text="AI match executions today")

    class Meta:
        unique_together = ("company", "usage_date")
        ordering = ["-usage_date"]

    def __str__(self) -> str:
        return (
            f"AI usage Company#{self.company_id} on {self.usage_date} = {self.ai_matches}"
        )


from .models_watchlist import TenderWatchlist  # noqa: E402, F401

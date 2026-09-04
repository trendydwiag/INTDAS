"""Pydantic v2 data models for SPSE Inaproc tender data."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class TenderPackage(BaseModel):
    """Stage 1 — Discovery data extracted from the DataTables listing endpoint.

    Fields map directly to the 16-column response of ``/dt/lelang``.
    """

    kode_instansi: str = Field(
        ...,
        description="Short code of the instansi, e.g. 'kemendagri', 'dki'.",
    )
    id_lelang: str = Field(
        ...,
        description="Unique tender/lelang identifier (kode).",
    )
    nama_paket: str = Field(
        default="",
        description="Name / title of the procurement package.",
    )
    instansi: str = Field(
        default="",
        description="Full instansi name as listed in SPSE.",
    )
    status: str = Field(
        default="",
        description="Current status of the tender package.",
    )
    nilai_pagu: int = Field(
        default=0,
        ge=0,
        description="Nilai pagu (ceiling budget) in IDR.",
    )
    kualifikasi: str = Field(
        default="",
        description="Qualification requirements.",
    )
    metode_pemilihan: str = Field(
        default="",
        description="Selection method (e.g. 'Tender Umum', 'Penunjukan Langsung').",
    )
    evaluasi: str = Field(
        default="",
        description="Evaluation method.",
    )
    jenis_pengadaan: str = Field(
        default="",
        description="Jenis pengadaan (Barang, Jasa, Pekerjaan Konstruksi, etc.).",
    )
    peserta: int = Field(
        default=0,
        ge=0,
        description="Number of participants.",
    )
    nilai_kontrak: int = Field(
        default=0,
        ge=0,
        description="Contract value in IDR.",
    )
    url_pengumuman: str = Field(
        default="",
        description="Full URL to the pengumuman (announcement) page.",
    )
    scraped_at: datetime = Field(
        default_factory=datetime.now,
        description="Timestamp when this record was scraped.",
    )

    model_config = {"str_strip_whitespace": True}

    @field_validator("nilai_pagu", "nilai_kontrak", mode="before")
    @classmethod
    def _parse_harga(cls, v: object) -> int:
        """Parse harga fields that may come as strings with dots/commas."""
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            cleaned = v.replace(".", "").replace(",", "").replace("Rp", "").replace(" ", "").strip()
            if cleaned.isdigit():
                return int(cleaned)
            return 0
        return 0

    def model_post_init(self, __context: object) -> None:
        """Auto-derive URL_pengumuman if not set."""
        if not self.url_pengumuman and self.id_lelang and self.kode_instansi:
            self.url_pengumuman = (
                f"https://spse.inaproc.id/{self.kode_instansi}"
                f"/lelang/{self.id_lelang}/pengumumanlelang"
            )


class TenderDetail(BaseModel):
    """Stage 2 — Detail data extracted from the pengumuman page."""

    kode_instansi: str = Field(
        ...,
        description="Short code of the instansi.",
    )
    id_lelang: str = Field(
        ...,
        description="Unique tender/lelang identifier.",
    )
    nama_paket: str = Field(
        default="",
        description="Name / title of the procurement package.",
    )
    instansi: str = Field(
        default="",
        description="Full instansi name.",
    )
    hps: int = Field(
        default=0,
        ge=0,
        description="Harga Perkiraan Sendiri (HPS) in IDR.",
    )
    jenis_pengadaan: str = Field(
        default="",
        description="Jenis pengadaan.",
    )
    metode_pengadaan: str = Field(
        default="",
        description="Metode pengadaan (e.g. Tender Umum).",
    )
    tahap_saat_ini: str = Field(
        ...,
        description="Current tender stage, normalised to lowercase.",
        examples=["pengumuman prakualifikasi"],
    )
    is_prakualifikasi: bool = Field(
        default=False,
        description="True when tahap_saat_ini matches a prakualifikasi keyword.",
    )
    kbli_code: str = Field(
        default="",
        description="KBLI 5-digit code extracted from qualification requirements.",
    )
    kbli_description: str = Field(
        default="",
        description="KBLI description matching the code.",
    )
    url_pengumuman: str = Field(
        default="",
        description="Full URL to the pengumuman page.",
    )
    requirement_text: str = Field(
        default="",
        description="Extracted requirement text from the pengumuman page for AI matching.",
    )
    is_it_priority: bool = Field(
        default=False,
        description="Auto-tagged True when package matches IT priority criteria.",
    )
    priority_score: int = Field(
        default=0,
        ge=0,
        le=100,
        description="IT priority score 0-100, higher = more IT-relevant.",
    )

    # Detail enrichment fields
    jadwal_json: list[dict] = Field(
        default_factory=list,
        description="Jadwal tahapan: [{no, tahap, mulai, sampai, perubahan}]",
    )
    syarat_kualifikasi: str = Field(
        default="",
        description="Cleaned qualification requirement text.",
    )
    peserta_count: int = Field(
        default=0,
        ge=0,
        description="Number of participants.",
    )
    participants: list[dict] = Field(
        default_factory=list,
        description="Participant records from the /peserta page: [{name, npwp}, ...]",
    )
    lokasi_pekerjaan: str = Field(
        default="",
        description="Work location from detail page.",
    )
    tahun_anggaran: str = Field(
        default="",
        description="Budget year, e.g. '2026'.",
    )
    satuan_kerja_detail: str = Field(
        default="",
        description="Satuan kerja from detail page.",
    )

    # Winner data (Phase 6B) — populated only for retained awarded tenders from
    # the verified SPSE /evaluasi/{id}/pemenang page. None for tenders that have
    # not reached a pemenang stage. Never fabricated:
    #   {company_name, npwp, alamat, harga_penawaran, harga_terkoreksi,
    #    harga_negosiasi, winning_value}
    winner: dict | None = Field(
        default=None,
        description="Awarded winner record from the /evaluasi/{id}/pemenang page (None when not awarded).",
    )
    winner_source_url: str = Field(
        default="",
        description="SPSE page the winner was read from (/evaluasi/{id}/pemenang).",
    )

    scraped_at: datetime = Field(
        default_factory=datetime.now,
        description="Timestamp when this detail was scraped.",
    )

    model_config = {"str_strip_whitespace": True}

    KBLI_MAP: dict[str, str] = {
        "62019": "Aktivitas Pemrograman Komputer Lainnya",
        "62090": "Aktivitas Teknologi Informasi Dan Jasa Komputer Lainnya",
        "62029": "Aktivitas Konsultasi Komputer dan Manajemen Fasilitas Komputer Lainnya",
    }

    PRAKUALIFIKASI_KEYWORDS: tuple[str, ...] = (
        "pengumuman prakualifikasi",
        "pembuktian kualifikasi",
        "kirim persyaratan kualifikasi",
        "prakualifikasi",
    )

    def detect_prakualifikasi(self) -> bool:
        """Check whether *tahap_saat_ini* matches any known prakualifikasi keyword."""
        lowered = self.tahap_saat_ini.lower().strip()
        return any(kw in lowered for kw in self.PRAKUALIFIKASI_KEYWORDS)

    def model_post_init(self, __context: object) -> None:
        """Auto-derive *is_prakualifikasi* from *tahap_saat_ini* and *kbli_description* from *kbli_code*."""
        self.is_prakualifikasi = self.detect_prakualifikasi()
        if self.kbli_code and not self.kbli_description:
            self.kbli_description = self.KBLI_MAP.get(self.kbli_code, "")
        if not self.url_pengumuman and self.id_lelang and self.kode_instansi:
            self.url_pengumuman = (
                f"https://spse.inaproc.id/{self.kode_instansi}"
                f"/lelang/{self.id_lelang}/pengumumanlelang"
            )

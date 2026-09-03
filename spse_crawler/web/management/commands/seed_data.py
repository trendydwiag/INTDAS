"""
Django management command to seed production data.

Creates:
  - Superadmin accounts
  - Company profiles with real data
  - Company qualifications (NIB, Izin Usaha, SBU, SDM, Pengalaman Kerja)
  - User accounts (company_admin, submitter) with proper roles
  - KBLI master codes for IT priority scoring

Usage:
    python manage.py seed_data
    python manage.py seed_data --flush   # Delete all seed data first
"""

import os

from django.core.management.base import BaseCommand
from django.db import transaction

# Documented default password used ONLY when neither SEED_PASSWORD /
# SPSE_SEED_PASSWORD env var nor --password is provided. It is stable (so
# repeated `seed_data` runs do not rotate credentials unexpectedly), but it is
# PUBLIC and MUST be changed after first login on any real deployment.
_DEFAULT_SEED_PASSWORD = "SpseSeed!2026"


def _resolve_seed_password(cli_password: str = "") -> str:
    """Resolve the stable seed password used for all seed accounts.

    Priority: --password CLI > SEED_PASSWORD > SPSE_SEED_PASSWORD > default.
    Returning the chosen value (with source) so the operator knows which one is
    in effect. The chosen password is applied deterministically on every run.
    """
    if cli_password:
        return cli_password, "--password"
    for var in ("SEED_PASSWORD", "SPSE_SEED_PASSWORD"):
        val = os.environ.get(var, "").strip()
        if val:
            return val, var
    return _DEFAULT_SEED_PASSWORD, "default (SpseSeed!2026)"



class Command(BaseCommand):
    help = "Seed production data (users, companies, qualifications, KBLI)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete all existing seed data before re-seeding",
        )
        parser.add_argument(
            "--password",
            default="",
            help="Stable password applied to all seed accounts. "
                 "Overrides SEED_PASSWORD / SPSE_SEED_PASSWORD. Falls back to "
                 "a documented default if unset (must then be changed on login).",
        )

    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        seed_password, source = _resolve_seed_password(options["password"])
        with transaction.atomic():
            companies = self._seed_companies()
            self._seed_qualifications(companies)
            self._seed_users(companies, seed_password)
            self._seed_kbli()

        self.stdout.write(self.style.SUCCESS("Seed data ready."))
        self.stdout.write(self.style.WARNING(
            "All seed accounts share the resolved seed password "
            f"(source: {source}). Change passwords after first login."
        ))

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------
    def _flush(self):
        from spse_crawler.accounts.models import User
        from spse_crawler.companies.models import CompanyProfile, CompanyQualification
        from spse_crawler.web.models import KbliMaster
        from spse_crawler.submissions.models import TenderSubmissionStatus
        from spse_crawler.audit.models import AuditLog

        self.stdout.write("Flushing seed data ...")
        TenderSubmissionStatus.objects.all().delete()
        AuditLog.objects.all().delete()
        User.objects.all().delete()
        CompanyQualification.objects.all().delete()
        CompanyProfile.objects.all().delete()
        KbliMaster.objects.all().delete()
        self.stdout.write("Flush complete.")

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------
    def _seed_companies(self):
        from spse_crawler.companies.models import CompanyProfile

        DATA = [
            {
                "name": "PT Khatulistiwa Nusantara Indonesia",
                "nib": "0220208762392",
                "npwp": "0823955182421000",
                "address": "Jl. Flamboyan Raya No.39, Kelurahan Cibabat, Kecamatan Cimahi Utara, Kota Cimahi.",
                "phone": "02220668202",
                "email": "admin@khansia.co.id",
                "contact_person": "Sugeng Pramono",
                "modal_disetor": 1_000_000_000,
                "penghasilan_tahunan": 4_000_000_000,
            },
            {
                "name": "PT Solusi Informatika Bersama",
                "nib": "NIB-PT002",
                "npwp": "88.777.666.5-444.002",
                "address": "Jl. Gatot Subroto No. 38, Jakarta Pusat",
                "phone": "021-31906060",
                "email": "contact@siberinsan.co.id",
                "contact_person": "Dewi Kartika",
                "modal_disetor": 10_000_000_000,
                "penghasilan_tahunan": 55_000_000_000,
            },
        ]

        companies = {}
        for d in DATA:
            obj, _ = CompanyProfile.objects.update_or_create(
                nib=d["nib"], defaults=d,
            )
            companies[obj.nib] = obj
            self.stdout.write(f"  Company: {obj.name} (id={obj.id})")
        return companies

    # ------------------------------------------------------------------
    # Qualifications
    # ------------------------------------------------------------------
    def _seed_qualifications(self, companies):
        from spse_crawler.companies.models import CompanyQualification

        khansia = companies["0220208762392"]
        siberinsan = companies["NIB-PT002"]

        DATA = [
            # --- PT Khatulistiwa Nusantara Indonesia ---
            {
                "company": khansia,
                "category": "izin_usaha",
                "name": "NIB 2020",
                "number": "0220208762392",
                "kbli_codes": ["46100", "46512", "46521", "58200", "46523", "62019", "46511", "62090", "62029"],
                "klasifikasi_usaha": "menengah",
                "value_amount": 1_000_000_000,
                "status": "active",
            },
            {
                "company": khansia,
                "category": "pengalaman_kerja",
                "name": "Pengadaan I-SURE System, WPI dan Sales Partnership Enhancement",
                "details": {"location": "Jakarta"},
                "project_name": "Pengadaan I-SURE System, WPI dan Sales Partnership Enhancement",
                "client_name": "PT Telkom Indonesia, Tbk",
                "project_year": 2026,
                "status": "active",
            },
            {
                "company": khansia,
                "category": "sdm",
                "name": "Praba",
                "details": {
                    "person_name": "Prabaswara Muktikanana Seta",
                    "position": "Project Manager",
                    "education": "s2",
                    "experience_years": 7,
                    "skk": "",
                },
                "status": "active",
            },
            {
                "company": khansia,
                "category": "sdm",
                "name": "Trendy",
                "details": {
                    "person_name": "Trendy Dwi Anugrah Gusti",
                    "position": "",
                    "education": "s1",
                    "experience_years": 7,
                    "skk": "Scrum Certified",
                },
                "status": "active",
            },
        ]

        for d in DATA:
            company = d.pop("company")
            details = d.pop("details", {})
            obj, created = CompanyQualification.objects.update_or_create(
                company=company,
                name=d["name"],
                category=d["category"],
                defaults={**d, "details": details},
            )
            label = "created" if created else "updated"
            self.stdout.write(f"  Qualification ({label}): [{obj.category}] {obj.name}")

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    def _seed_users(self, companies, seed_password: str):
        from spse_crawler.accounts.models import User

        khansia = companies["0220208762392"]
        siberinsan = companies["NIB-PT002"]

        USERS = [
            {
                "username": "superadmin",
                "email": "superadmin@spse.test",
                "first_name": "Super",
                "last_name": "Admin",
                "role": "superadmin",
                "is_superuser": True,
                "is_staff": True,
                "company": None,
            },
            {
                "username": "admin",
                "email": "admin@example.com",
                "first_name": "",
                "last_name": "",
                "role": "superadmin",
                "is_superuser": True,
                "is_staff": True,
                "company": None,
            },
            {
                "username": "admin_pt1",
                "email": "admin@khansia.co.id",
                "first_name": "Sugeng",
                "last_name": "Pramono",
                "role": "company_admin",
                "is_superuser": False,
                "is_staff": True,
                "company": khansia,
            },
            {
                "username": "submitter_pt1",
                "email": "budhi@khansia.co.id",
                "first_name": "Budhi",
                "last_name": "Utomo",
                "role": "submitter",
                "is_superuser": False,
                "is_staff": False,
                "company": khansia,
            },
            {
                "username": "admin_pt2",
                "email": "admin@siberinsan.co.id",
                "first_name": "Dewi",
                "last_name": "Kartika",
                "role": "company_admin",
                "is_superuser": False,
                "is_staff": True,
                "company": siberinsan,
            },
            {
                "username": "submitter_pt2",
                "email": "submitter@siberinsan.co.id",
                "first_name": "Budi",
                "last_name": "Santoso",
                "role": "submitter",
                "is_superuser": False,
                "is_staff": False,
                "company": siberinsan,
            },
        ]

        for u in USERS:
            company = u.pop("company")
            is_superuser = u.pop("is_superuser")
            password = seed_password

            obj, created = User.objects.get_or_create(
                username=u["username"],
                defaults={
                    **u,
                    "company": company,
                    "is_superuser": is_superuser,
                },
            )
            obj.set_password(password)
            obj.save()

            label = "created" if created else "updated"
            company_label = company.name if company else "(none)"
            self.stdout.write(
                f"  User ({label}): {obj.username} — "
                f"role={obj.role} company={company_label}"
            )

    # ------------------------------------------------------------------
    # KBLI Master
    # ------------------------------------------------------------------
    def _seed_kbli(self):
        from spse_crawler.web.models import KbliMaster

        KBLI = [
            ("46100", "Perdagangan Besar Atas Dasar Balas Jasa (Fee) Atau Kontrak", True),
            ("46511", "Perdagangan Besar Komputer dan Perlengkapan Komputer", True),
            ("46512", "Perdagangan Besar Piranti Lunak", True),
            ("46521", "Perdagangan Besar Suku Cadang Elektronik", True),
            ("46523", "Perdagangan Besar Peralatan Telekomunikasi", True),
            ("58200", "Penerbitan piranti lunak (Software)", True),
            ("62019", "Aktivitas Pemrograman Komputer Lainnya", True),
            ("62029", "Aktivitas Konsultasi Komputer dan Manajemen Fasilitas Komputer Lainnya", True),
            ("62090", "Aktivitas Teknologi Informasi Dan Jasa Komputer Lainnya", True),
        ]

        for code, name, active in KBLI:
            obj, created = KbliMaster.objects.update_or_create(
                code=code, defaults={"name": name, "is_active": active},
            )
            label = "created" if created else "updated"
            self.stdout.write(f"  KBLI ({label}): {code} — {name}")

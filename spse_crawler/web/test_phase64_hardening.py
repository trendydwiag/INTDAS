"""Tests for Phase 6.4 — Production Deployment Hardening.

Covers:
  1. Single-process scheduler management command (`run_scheduler`) lifecycle.
  2. AI provider fallback provenance tracking (`is_fallback`, `fallback_reason`).
  3. Distinguishing genuine LLM results from fallback results in `AIMatchResult` and `TenderResult`.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.core.management import call_command
from spse_crawler.web.models import TenderResult, IntelligenceJob
from spse_crawler.companies.models import CompanyProfile
from spse_crawler.ai_match.models import AIMatchResult
from spse_crawler.ai_match.providers import get_provider, RuleBasedProvider, OpenAIProvider
from spse_crawler.ai_match.matcher import run_match


class AIProviderFallbackHardeningTestCase(TestCase):
    """Verify AI provider fallback provenance and observability."""

    def test_get_provider_explicit_rule_based(self):
        """Explicitly configured rule_based provider is not a fallback."""
        with patch.dict(os.environ, {"AI_PROVIDER": "rule_based"}, clear=False):
            if "AI_API_KEY" in os.environ:
                del os.environ["AI_API_KEY"]
            provider = get_provider()
            assert isinstance(provider, RuleBasedProvider)
            assert provider.is_fallback is False
            assert provider.fallback_reason == ""

    def test_get_provider_missing_key_fallback(self):
        """Missing API key for OpenAI triggers explicit fallback flag and reason."""
        env = dict(os.environ)
        env["AI_PROVIDER"] = "openai"
        if "AI_API_KEY" in env:
            del env["AI_API_KEY"]
        with patch.dict(os.environ, env, clear=True):
            provider = get_provider()
            assert isinstance(provider, RuleBasedProvider)
            assert provider.is_fallback is True
            assert "init failed" in provider.fallback_reason

    def test_get_provider_init_error_fallback(self):
        """Provider init error triggers explicit fallback metadata."""
        env = dict(os.environ)
        env["AI_PROVIDER"] = "openai"
        env["AI_API_KEY"] = "testkey"
        with patch.dict(os.environ, env, clear=True):
            with patch("spse_crawler.ai_match.providers.OpenAIProvider", side_effect=ValueError("Invalid config")):
                provider = get_provider()
                assert isinstance(provider, RuleBasedProvider)
                assert provider.is_fallback is True
                assert "Invalid config" in provider.fallback_reason

    def test_run_match_persists_fallback_provenance(self):
        """run_match persists is_fallback and fallback_reason into AIMatchResult and TenderResult."""
        env = dict(os.environ)
        env["AI_PROVIDER"] = "openai"
        if "AI_API_KEY" in env:
            del env["AI_API_KEY"]

        with patch.dict(os.environ, env, clear=True):
            tender = TenderResult.objects.create(
                kode_instansi="testinst",
                id_lelang="8888001",
                nama_paket="Pengadaan Server IT",
                hps=500000000,
                tahap_saat_ini="pengumuman prakualifikasi [...]",
            )
            company = CompanyProfile.objects.create(
                name="PT IT Solusindo",
                npwp="010000000000000",
            )

            result = run_match(tender.id, company.id, force=True)
            assert result["is_fallback"] is True
            assert "init failed" in result["fallback_reason"]

            match_obj = AIMatchResult.objects.get(tender=tender, company=company)
            assert match_obj.is_fallback is True
            assert "init failed" in match_obj.fallback_reason

            tender.refresh_from_db()
            assert tender.ai_analysis_json["is_fallback"] is True
            assert "init failed" in tender.ai_analysis_json["fallback_reason"]

    def test_llm_runtime_error_fallback(self):
        """Runtime error during LLM call marks result as fallback."""
        tender = TenderResult.objects.create(
            kode_instansi="testinst",
            id_lelang="8888002",
            nama_paket="Pengadaan Jaringan Fiber",
            hps=300000000,
            tahap_saat_ini="pengumuman prakualifikasi [...]",
        )
        company = CompanyProfile.objects.create(
            name="PT Fiberindo",
            npwp="020000000000000",
        )

        with patch("spse_crawler.ai_match.providers.RuleBasedProvider.chat", side_effect=TimeoutError("504 Gateway Timeout")):
            result = run_match(tender.id, company.id, force=True)
            assert result["is_fallback"] is True
            assert "504 Gateway Timeout" in result["fallback_reason"]

            match_obj = AIMatchResult.objects.get(tender=tender, company=company)
            assert match_obj.is_fallback is True
            assert "504 Gateway Timeout" in match_obj.fallback_reason


class SchedulerDaemonHardeningTestCase(TestCase):
    """Verify single-process scheduler daemon lifecycle."""

    def test_run_scheduler_command_help(self):
        """run_scheduler management command is registered and outputs help."""
        from io import StringIO
        out = StringIO()
        with pytest.raises(SystemExit) as exc:
            call_command("run_scheduler", "--help", stdout=out)
        assert exc.value.code == 0

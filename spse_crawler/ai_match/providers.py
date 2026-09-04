import hashlib
import json
import math
import os
import re
from abc import ABC, abstractmethod
from collections import Counter

from loguru import logger


class AIProvider(ABC):
    """Abstract base class for LLM providers."""

    is_fallback: bool = False
    fallback_reason: str = ""

    @abstractmethod
    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Send a chat completion request and return the response text."""
        ...

    @abstractmethod
    def name(self) -> str:
        ...


class OpenAIProvider(AIProvider):
    """OpenAI-compatible API provider (works with any OpenAI-compatible endpoint)."""

    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str = ""):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url

    def name(self) -> str:
        return "openai"

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        import openai

        kwargs = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        client = openai.OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        return response.choices[0].message.content or ""


class GeminiProvider(AIProvider):
    """Google Gemini API provider."""

    def __init__(self, api_key: str, model: str = "gemini-1.5-pro"):
        self._api_key = api_key
        self._model = model

    def name(self) -> str:
        return "gemini"

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        import google.generativeai as genai

        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(
            self._model,
            generation_config=genai.GenerationConfig(
                temperature=0.3,
                max_output_tokens=2000,
            ),
        )
        combined = f"{system_prompt}\n\n{user_prompt}"
        response = model.generate_content(combined)
        return response.text or ""


class AnthropicProvider(AIProvider):
    """Anthropic Claude API provider."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self._api_key = api_key
        self._model = model

    def name(self) -> str:
        return "anthropic"

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text if response.content else ""


class FallbackProvider(AIProvider):
    """Fallback provider that always returns NOT_READY / score 0 — used when LLM is unavailable."""

    def name(self) -> str:
        return "fallback"

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        return json.dumps({
            "eligibility_status": "NOT_READY",
            "mandatory_passed": False,
            "fit_score": 0,
            "blockers": ["AI analysis unavailable — provider not configured or API error."],
            "missing_requirements": [],
            "recommended_actions": ["Konfigurasi provider AI atau gunakan RuleBased offline."],
            "summary": "AI analysis unavailable — provider not configured or API error.",
            "criteria": [],
        })


class RuleBasedProvider(AIProvider):
    """Deterministic Hard Gate + Local TF-IDF Soft Technical Fit Provider.

    No external API needed — 100% offline, zero cost.
    Separates:
      1. Hard Requirements (KBLI, SBU, NIB/Izin, NPWP): deterministic verification
         against actual company qualifications (NOT text similarity).
      2. Soft Requirements: TF-IDF cosine similarity + keyword category overlap
         as technical relevance signal only.
    """

    _STOP_WORDS = frozenset({
        "dan", "di", "ke", "dari", "yang", "untuk", "dengan", "pada", "adalah",
        "ini", "itu", "atau", "akan", "telah", "dalam", "tidak", "bisa", "juga",
        "oleh", "karena", "mereka", "kami", "kita", "anda", "ia", "hal", "cara",
        "program", "upa", "upaya", "per", "tiap", "setiap", "lebih", "bagi",
        "antara", "serta", "namun", "tetapi", "jika", "maka", "sebagai",
        "menjadi", "harus", "wajib", "para", "tersebut", "sesuai",
    })

    _KEYWORD_CATEGORIES = {
        "kualifikasi_usaha": ["izin", "kualifikasi", "sbu", "usaha", "terdaftar"],
        "pengalaman": ["pengalaman", "track", "record", "portofolio", "proyek"],
        "keuangan": ["keuangan", "neraca", "labarugi", "omzet", "modal", "aset"],
        "sdm": ["sdm", "personil", "tenaga", "ahli", "certified", "sertifikasi"],
        "teknis": ["sistem", "informasi", "teknologi", "komputer", "jaringan", "software", "hardware"],
    }

    def name(self) -> str:
        return "rule_based"

    def _tokenize(self, text: str) -> list[str]:
        text = text.lower()
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        tokens = text.split()
        return [t for t in tokens if t not in self._STOP_WORDS and len(t) > 1]

    def _compute_tf(self, tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        total = len(tokens) or 1
        return {word: count / total for word, count in counts.items()}

    def _compute_idf(self, docs: list[list[str]]) -> dict[str, float]:
        n_docs = len(docs) or 1
        doc_freq: dict[str, int] = {}
        for doc in docs:
            unique = set(doc)
            for word in unique:
                doc_freq[word] = doc_freq.get(word, 0) + 1
        return {word: math.log(n_docs / df) for word, df in doc_freq.items()}

    def _tfidf_vector(self, tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
        tf = self._compute_tf(tokens)
        return {word: tf_val * idf.get(word, 0.0) for word, tf_val in tf.items()}

    def _cosine_similarity(self, vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
        common = set(vec_a.keys()) & set(vec_b.keys())
        dot = sum(vec_a[w] * vec_b[w] for w in common)
        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _keyword_match_score(self, req_tokens: list[str], qual_tokens: list[str]) -> float:
        req_set = set(req_tokens)
        qual_set = set(qual_tokens)
        if not req_set:
            return 0.0

        category_scores = []
        for _cat, keywords in self._KEYWORD_CATEGORIES.items():
            req_hits = sum(1 for kw in keywords if kw in req_set or any(kw in t for t in req_set))
            qual_hits = sum(1 for kw in keywords if kw in qual_set or any(kw in t for t in qual_set))
            if req_hits > 0 and qual_hits > 0:
                category_scores.append(min(req_hits, qual_hits) / max(req_hits, qual_hits))
            elif req_hits > 0 and qual_hits == 0:
                category_scores.append(0.0)

        if not category_scores:
            return 0.0
        return sum(category_scores) / len(self._KEYWORD_CATEGORIES)

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Evaluate qualification fit using deterministic hard gates + TF-IDF soft relevance."""
        try:
            # 1. Parse requirement text from user_prompt
            req_match = re.search(
                r'== PERSYARATAN KUALIFIKASI SPSE ==\s*\n(.+?)(?=\n== PROFIL PERUSAHAAN)',
                user_prompt,
                re.DOTALL,
            )
            req_text = req_match.group(1).strip() if req_match else user_prompt[:3000]

            # 2. Parse company profile basics
            company_section_match = re.search(
                r'== PROFIL PERUSAHAAN ==\s*\n(.+?)(?=\n== KUALIFIKASI)',
                user_prompt,
                re.DOTALL,
            )
            company_section = company_section_match.group(1) if company_section_match else ""
            nib_match = re.search(r'NIB:\s*([^\n]+)', company_section)
            npwp_match = re.search(r'NPWP:\s*([^\n]+)', company_section)
            company_nib = nib_match.group(1).strip() if nib_match else ""
            company_npwp = npwp_match.group(1).strip() if npwp_match else ""

            # 3. Parse qualifications JSON list
            qual_json_match = re.search(
                r'== KUALIFIKASI[^\n]*==\s*\n(.*?)(\n\s*Perform strict|\n\s*Analyze|\Z)',
                user_prompt,
                re.DOTALL,
            )
            quals = []
            if qual_json_match:
                try:
                    quals = json.loads(qual_json_match.group(1).strip())
                except Exception:
                    quals = []

            # Check if company data is missing
            if not quals and not (company_nib and len(company_nib) >= 5):
                return json.dumps({
                    "eligibility_status": "NOT_READY",
                    "mandatory_passed": False,
                    "fit_score": 0,
                    "blockers": ["Tidak ada data kualifikasi perusahaan."],
                    "missing_requirements": [],
                    "recommended_actions": ["Lengkapi profil kualifikasi perusahaan."],
                    "summary": "Profil kualifikasi perusahaan belum diisi.",
                    "criteria": [],
                })

            # Extract structured company qualification indices
            company_kblis: set[str] = set()
            for q in quals:
                for code in q.get("kbli_codes", []):
                    if code:
                        company_kblis.add(str(code).strip())
                if q.get("kbli_code"):
                    company_kblis.add(str(q.get("kbli_code")).strip())

            def _is_sbu(q):
                cat = str(q.get("category_raw") or q.get("category", "")).lower()
                name = str(q.get("name", "")).lower()
                return "sbu" in cat or "sbu" in name or "badan usaha" in cat or "badan usaha" in name

            def _is_izin(q):
                cat = str(q.get("category_raw") or q.get("category", "")).lower()
                name = str(q.get("name", "")).lower()
                return "izin" in cat or "izin" in name or "nib" in cat or "nib" in name or "siup" in cat or "siup" in name

            has_sbu = any(_is_sbu(q) for q in quals)
            has_izin = any(_is_izin(q) for q in quals)
            has_exp = any(q.get("category_raw") == "pengalaman_kerja" or "pengalaman" in str(q.get("category", "")).lower() for q in quals)

            criteria = []
            blockers = []
            missing_reqs = []
            actions = []

            req_lower = req_text.lower()

            # -----------------------------------------------------------------
            # HARD GATE 1: KBLI Code Verification
            # -----------------------------------------------------------------
            # Look for explicit KBLI code in requirement text
            tender_kbli_match = re.search(r'kbli[:\s]+([0-9]{5})', req_lower)
            if not tender_kbli_match:
                tender_kbli_match = re.search(r'\b(62[0-9]{3}|63[0-9]{3}|61[0-9]{3}|58[0-9]{3}|41[0-9]{3}|42[0-9]{3}|43[0-9]{3})\b', req_text)

            if tender_kbli_match:
                required_kbli = tender_kbli_match.group(1)
                if required_kbli in company_kblis:
                    criteria.append({
                        "requirement": f"Kesesuaian KBLI ({required_kbli})",
                        "category": "KBLI",
                        "mandatory": True,
                        "status": "pass",
                        "evidence": f"KBLI {required_kbli} aktif terdaftar pada profil perusahaan.",
                        "action": None,
                    })
                else:
                    criteria.append({
                        "requirement": f"Kesesuaian KBLI ({required_kbli})",
                        "category": "KBLI",
                        "mandatory": True,
                        "status": "fail",
                        "evidence": f"Perusahaan memiliki KBLI: {', '.join(sorted(company_kblis)) or 'tidak ada'}.",
                        "action": f"Tambahkan KBLI {required_kbli} pada profil perusahaan jika bidang usaha sesuai.",
                    })
                    blockers.append(f"KBLI mismatch: Tender membutuhkan KBLI {required_kbli}, perusahaan tidak memilikinya.")
                    actions.append(f"Lengkapi KBLI {required_kbli} di profil perusahaan.")

            # -----------------------------------------------------------------
            # HARD GATE 2: SBU Verification
            # -----------------------------------------------------------------
            requires_sbu = bool(re.search(r'\b(sbu|sertifikat badan usaha)\b', req_lower))
            if requires_sbu:
                if has_sbu:
                    criteria.append({
                        "requirement": "Kepemilikan SBU (Sertifikat Badan Usaha) aktif",
                        "category": "SBU",
                        "mandatory": True,
                        "status": "pass",
                        "evidence": "Dokumen SBU aktif terdaftar pada profil kualifikasi.",
                        "action": None,
                    })
                else:
                    criteria.append({
                        "requirement": "Kepemilikan SBU (Sertifikat Badan Usaha) aktif",
                        "category": "SBU",
                        "mandatory": True,
                        "status": "fail",
                        "evidence": "Tidak ada dokumen SBU tercatat pada profil perusahaan.",
                        "action": "Unggah dan verifikasi SBU yang sesuai dengan subklasifikasi tender.",
                    })
                    blockers.append("SBU tidak ditemukan pada profil perusahaan.")
                    actions.append("Unggah dokumen SBU yang masih berlaku.")

            # -----------------------------------------------------------------
            # HARD GATE 3: NIB / Izin Usaha Verification
            # -----------------------------------------------------------------
            requires_license = bool(re.search(r'\b(nib|nomor induk berusaha|izin usaha|siup|iujk)\b', req_lower))
            if requires_license:
                if (company_nib and len(company_nib) >= 5) or has_izin:
                    criteria.append({
                        "requirement": "Izin Usaha / NIB (Nomor Induk Berusaha) aktif",
                        "category": "BUSINESS_LICENSE",
                        "mandatory": True,
                        "status": "pass",
                        "evidence": f"NIB {company_nib} / Izin Usaha aktif tercatat.",
                        "action": None,
                    })
                else:
                    criteria.append({
                        "requirement": "Izin Usaha / NIB (Nomor Induk Berusaha) aktif",
                        "category": "BUSINESS_LICENSE",
                        "mandatory": True,
                        "status": "fail",
                        "evidence": "NIB / Izin usaha tidak ditemukan pada profil perusahaan.",
                        "action": "Lengkapi NIB atau Izin Usaha perusahaan.",
                    })
                    blockers.append("NIB / Izin Usaha tidak ditemukan.")
                    actions.append("Lengkapi data NIB dan Izin Usaha.")

            # -----------------------------------------------------------------
            # HARD GATE 4: NPWP / Legalitas Keuangan
            # -----------------------------------------------------------------
            requires_npwp = bool(re.search(r'\b(npwp|nomor pokok wajib pajak|pajak)\b', req_lower))
            if requires_npwp:
                if company_npwp and len(company_npwp) >= 8:
                    criteria.append({
                        "requirement": "Nomor Pokok Wajib Pajak (NPWP)",
                        "category": "FINANCIAL",
                        "mandatory": True,
                        "status": "pass",
                        "evidence": f"NPWP {company_npwp} valid.",
                        "action": None,
                    })
                else:
                    criteria.append({
                        "requirement": "Nomor Pokok Wajib Pajak (NPWP)",
                        "category": "FINANCIAL",
                        "mandatory": True,
                        "status": "fail",
                        "evidence": "NPWP perusahaan belum diisi atau tidak valid.",
                        "action": "Lengkapi nomor NPWP perusahaan di profil.",
                    })
                    blockers.append("NPWP perusahaan tidak tersedia.")
                    actions.append("Lengkapi NPWP perusahaan.")

            # -----------------------------------------------------------------
            # SOFT FIT: TF-IDF Cosine Similarity & Keyword Category Overlap
            # -----------------------------------------------------------------
            qual_text = json.dumps(quals, ensure_ascii=False)
            req_tokens = self._tokenize(req_text)
            qual_tokens = self._tokenize(qual_text)

            tfidf_sim = 0.0
            kw_score = 0.0
            if req_tokens and qual_tokens:
                all_docs = [req_tokens, qual_tokens]
                idf = self._compute_idf(all_docs)
                req_vec = self._tfidf_vector(req_tokens, idf)
                qual_vec = self._tfidf_vector(qual_tokens, idf)
                tfidf_sim = self._cosine_similarity(req_vec, qual_vec)
                kw_score = self._keyword_match_score(req_tokens, qual_tokens)

            soft_sim = (0.6 * tfidf_sim) + (0.4 * kw_score)

            # Extract sample technical requirements from text
            sentences = [s.strip() for s in re.split(r'[.\n]+', req_text) if len(s.strip()) > 15]
            qual_set = set(qual_tokens)
            for s in sentences[:5]:
                s_lower = s.lower()
                # Skip sentences that were already handled by hard gates
                if any(kw in s_lower for kw in ["kbli", "sbu", "npwp", "nomor pokok", "induk berusaha"]):
                    continue
                s_tokens = self._tokenize(s)
                overlap = len(set(s_tokens) & qual_set)
                tot = len(set(s_tokens)) or 1
                ratio = overlap / tot
                if ratio > 0.3:
                    stat = "pass"
                    act = None
                elif ratio > 0.1:
                    stat = "needs_action"
                    act = "Periksa kesiapan dokumen teknis pendukung"
                    missing_reqs.append(s[:80])
                else:
                    stat = "needs_action"
                    act = "Perlu verifikasi dokumen pendukung sebelum penawaran"
                    missing_reqs.append(s[:80])

                criteria.append({
                    "requirement": s[:120],
                    "category": "TECHNICAL" if any(w in s_lower for w in ["teknis", "sistem", "software", "alat", "metode"]) else "EXPERIENCE",
                    "mandatory": False,
                    "status": stat,
                    "evidence": f"Kesesuaian profil teknis: {overlap}/{tot} istilah cocok." if overlap else "Belum ditemukan bukti eksplisit.",
                    "action": act,
                })

            # -----------------------------------------------------------------
            # FINAL STATUS & FIT SCORE SYNTHESIS
            # -----------------------------------------------------------------
            if not quals and not company_nib:
                eligibility_status = "NOT_READY"
                mandatory_passed = False
                fit_score = 0
                summary = "Profil kualifikasi perusahaan belum diisi."
                blockers.append("Tidak ada data kualifikasi perusahaan.")
            elif blockers:
                eligibility_status = "NOT_ELIGIBLE"
                mandatory_passed = False
                # Hard failure strictly caps soft fit score at max 25
                fit_score = min(25, int(soft_sim * 25))
                summary = f"Tidak memenuhi syarat mutlak ({len(blockers)} kendala fatal: {'; '.join(blockers[:2])})"
            elif any(c["status"] == "needs_action" for c in criteria if c.get("mandatory")):
                eligibility_status = "CONDITIONALLY_ELIGIBLE"
                mandatory_passed = True
                fit_score = int(35 + (35 * min(1.0, soft_sim * 2.0)))
                summary = f"Memenuhi syarat bersyarat — verifikasi dokumen mutlak diperlukan (Skor Teknis: {fit_score}%)"
            else:
                eligibility_status = "ELIGIBLE"
                mandatory_passed = True
                fit_score = int(50 + (50 * min(1.0, soft_sim * 2.0)))
                summary = f"Memenuhi seluruh persyaratan kualifikasi tender (Skor Teknis: {fit_score}%)"

            return json.dumps({
                "eligibility_status": eligibility_status,
                "mandatory_passed": mandatory_passed,
                "fit_score": fit_score,
                "blockers": blockers,
                "missing_requirements": missing_reqs[:5],
                "recommended_actions": actions[:5] or (["Siapkan berkas penawaran."] if mandatory_passed else ["Penuhi kendala kualifikasi mutlak."]),
                "summary": summary,
                "criteria": criteria,
            })

        except Exception as exc:
            logger.error("[AI] RuleBased matching failed: {}", exc)
            return json.dumps({
                "eligibility_status": "NOT_READY",
                "mandatory_passed": False,
                "fit_score": 0,
                "blockers": [f"Rule-based analysis error: {exc}"],
                "missing_requirements": [],
                "recommended_actions": ["Coba jalankan analisis kembali."],
                "summary": f"Rule-based analysis error: {exc}",
                "criteria": [],
            })


def get_provider(provider_name: str | None = None) -> AIProvider:
    """Factory: instantiate the configured LLM provider from environment variables.

    Auto-detection priority:
      1. Explicit provider_name or AI_PROVIDER env var
      2. Gemini Free Tier (default if AI_API_KEY is set)
      3. RuleBasedProvider (free, always available — zero cost fallback)

    If the configured LLM provider fails to initialize, auto-fallback to RuleBasedProvider.
    """
    provider = provider_name or os.environ.get("AI_PROVIDER", "").strip()
    api_key = os.environ.get("AI_API_KEY", "").strip()
    model = os.environ.get("AI_MODEL", "")
    base_url = os.environ.get("AI_BASE_URL", "").strip()

    # Auto-detect: if no provider specified but API key exists, default to openai-compatible
    if not provider and api_key:
        provider = "openai"
        logger.info("[AI] No AI_PROVIDER set, API key found — defaulting to OpenAI-compatible provider")

    try:
        if provider == "openai":
            if not api_key:
                raise ValueError("AI_API_KEY not set for OpenAI provider")
            return OpenAIProvider(api_key=api_key, model=model or "gpt-4o", base_url=base_url)
        elif provider == "gemini":
            if not api_key:
                raise ValueError("AI_API_KEY not set for Gemini provider")
            return GeminiProvider(api_key=api_key, model=model or "gemini-1.5-flash")
        elif provider == "anthropic":
            if not api_key:
                raise ValueError("AI_API_KEY not set for Anthropic provider")
            return AnthropicProvider(api_key=api_key, model=model or "claude-sonnet-4-20250514")
        elif provider == "rule_based":
            return RuleBasedProvider()
        else:
            # No provider configured and no API key → use free rule-based engine
            logger.info("[AI] No provider/API key configured — using free RuleBased engine")
            return RuleBasedProvider()
    except Exception as exc:
        reason = f"Provider '{provider}' init failed: {exc}"
        logger.warning("[AI] {}, falling back to RuleBased engine", reason)
        fallback_p = RuleBasedProvider()
        fallback_p.is_fallback = True
        fallback_p.fallback_reason = reason
        return fallback_p

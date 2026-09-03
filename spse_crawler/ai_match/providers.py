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
    """Fallback provider that always returns score 0 — used when LLM is unavailable."""

    def name(self) -> str:
        return "fallback"

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        return json.dumps({
            "fit_score": 0,
            "summary": "AI analysis unavailable — provider not configured or API error.",
            "criteria": [],
        })


class RuleBasedProvider(AIProvider):
    """Free local rule-based matching using TF-IDF cosine similarity.

    No external API needed — 100% offline, zero cost.
    Combines TF-IDF similarity with keyword boost scoring.
    """

    # Indonesian stop words to filter out
    _STOP_WORDS = frozenset({
        "dan", "di", "ke", "dari", "yang", "untuk", "dengan", "pada", "adalah",
        "ini", "itu", "atau", "akan", "telah", "dalam", "tidak", "bisa", "juga",
        "oleh", "karena", "mereka", "kami", "kita", "anda", "ia", "hal", "cara",
        "program", "upa", "upaya", "per", "tiap", "setiap", "lebih", "bagi",
        "antara", "serta", "serta", "namun", "tetapi", "jika", "maka", "karena",
        "sebagai", "menjadi", "harus", "wajib", "para", "tersebut", "sesuai",
    })

    # Keyword categories for requirement matching
    _KEYWORD_CATEGORIES = {
        "kualifikasi_usaha": ["izin", "kualifikasi", "sbu", "usaha", "terdaftar"],
        "pengalaman": ["pengalaman", "track", "record", "portofolio", "proyek"],
        "keuangan": ["keuangan", "neraca", "labarugi", " omzet", "modal", "aset"],
        "sdm": ["sdm", "personil", "tenaga", "ahli", "certified", "sertifikasi"],
        "teknis": ["sistem", "informasi", "teknologi", "komputer", "jaringan", "software", "hardware"],
    }

    def name(self) -> str:
        return "rule_based"

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text: lowercase, remove punctuation, split, filter stop words."""
        text = text.lower()
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        tokens = text.split()
        return [t for t in tokens if t not in self._STOP_WORDS and len(t) > 1]

    def _compute_tf(self, tokens: list[str]) -> dict[str, float]:
        """Compute term frequency for a document."""
        counts = Counter(tokens)
        total = len(tokens) or 1
        return {word: count / total for word, count in counts.items()}

    def _compute_idf(self, docs: list[list[str]]) -> dict[str, float]:
        """Compute inverse document frequency across documents."""
        n_docs = len(docs) or 1
        doc_freq: dict[str, int] = {}
        for doc in docs:
            unique = set(doc)
            for word in unique:
                doc_freq[word] = doc_freq.get(word, 0) + 1
        return {word: math.log(n_docs / df) for word, df in doc_freq.items()}

    def _tfidf_vector(self, tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
        """Compute TF-IDF vector for a document."""
        tf = self._compute_tf(tokens)
        return {word: tf_val * idf.get(word, 0.0) for word, tf_val in tf.items()}

    def _cosine_similarity(self, vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
        """Compute cosine similarity between two sparse vectors."""
        common = set(vec_a.keys()) & set(vec_b.keys())
        dot = sum(vec_a[w] * vec_b[w] for w in common)
        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _keyword_match_score(self, req_tokens: list[str], qual_tokens: list[str]) -> float:
        """Score based on keyword category overlap."""
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

    def _build_criteria(self, req_text: str, qual_text: str, req_tokens: list[str], qual_tokens: list[str]) -> list[dict]:
        """Build per-requirement criteria evaluation."""
        criteria = []
        qual_set = set(qual_tokens)

        req_sentences = re.split(r'[.\n]+', req_text)
        req_sentences = [s.strip() for s in req_sentences if len(s.strip()) > 10]

        for sentence in req_sentences[:8]:
            sent_tokens = self._tokenize(sentence)
            overlap = len(set(sent_tokens) & qual_set)
            total = len(set(sent_tokens)) or 1
            match_ratio = overlap / total

            if match_ratio > 0.3:
                status = "pass"
            elif match_ratio > 0.1:
                status = "needs_action"
            else:
                status = "fail"

            criteria.append({
                "requirement": sentence[:120],
                "status": status,
                "evidence": f"Keyword overlap: {overlap}/{total} terms matched",
                "action": None if status == "pass" else "Periksa kualifikasi terkait",
            })

        return criteria

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Analyze qualification fit using TF-IDF + keyword matching."""
        try:
            # Extract texts from user_prompt (following the USER_PROMPT_TEMPLATE format)
            req_match = re.search(r'PERSYARATAN KUALIFIKASI SPSE\s*\n(.+?)(?=\n== PROFIL PERUSAHAAN)', user_prompt, re.DOTALL)
            qual_match = re.search(r'KUALIFASI PERUSAHAAN\s*\n(.+?)(?=\nAnalyze|\n\nAnalyze)', user_prompt, re.DOTALL)

            req_text = req_match.group(1).strip() if req_match else user_prompt[:3000]
            qual_text = qual_match.group(1).strip() if qual_match else user_prompt[:3000]

            if not qual_text or len(qual_text) < 10:
                return json.dumps({
                    "fit_score": 0,
                    "summary": "Tidak ada kualifikasi perusahaan yang tersedia untuk dianalisis.",
                    "criteria": [],
                })

            # Tokenize
            req_tokens = self._tokenize(req_text)
            qual_tokens = self._tokenize(qual_text)

            if not req_tokens or not qual_tokens:
                return json.dumps({
                    "fit_score": 0,
                    "summary": "Gagal mengekstrak teks dari persyaratan atau kualifikasi.",
                    "criteria": [],
                })

            # TF-IDF cosine similarity
            all_docs = [req_tokens, qual_tokens]
            idf = self._compute_idf(all_docs)
            req_vec = self._tfidf_vector(req_tokens, idf)
            qual_vec = self._tfidf_vector(qual_tokens, idf)
            tfidf_sim = self._cosine_similarity(req_vec, qual_vec)

            # Keyword category match
            kw_score = self._keyword_match_score(req_tokens, qual_tokens)

            # Combined score (60% TF-IDF + 40% keyword match)
            combined = 0.6 * tfidf_sim + 0.4 * kw_score
            fit_score = int(min(100, max(0, combined * 100)))

            # Build criteria
            criteria = self._build_criteria(req_text, qual_text, req_tokens, qual_tokens)

            # Generate summary
            pass_count = sum(1 for c in criteria if c["status"] == "pass")
            fail_count = sum(1 for c in criteria if c["status"] == "fail")
            total_criteria = len(criteria) or 1

            if fit_score >= 70:
                summary = f"Cocok — {pass_count}/{total_criteria} persyaratan terpenuhi (TF-IDF: {tfidf_sim:.2f}, Keyword: {kw_score:.2f})"
            elif fit_score >= 40:
                summary = f"Perlu evaluasi — {pass_count}/{total_criteria} cocok, {fail_count} belum terpenuhi (TF-IDF: {tfidf_sim:.2f})"
            else:
                summary = f"Kurang cocok — hanya {pass_count}/{total_criteria} persyaratan terpenuhi (TF-IDF: {tfidf_sim:.2f}, Keyword: {kw_score:.2f})"

            return json.dumps({
                "fit_score": fit_score,
                "summary": summary,
                "criteria": criteria,
            })

        except Exception as exc:
            logger.error("[AI] RuleBased matching failed: {}", exc)
            return json.dumps({
                "fit_score": 0,
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

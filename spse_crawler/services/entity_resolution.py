"""Conservative Entity Resolution & NPWP normalization.

Phase 6C — Company Identity, Entity Resolution & Company Intelligence.

Identity resolution is intentionally CONSERVATIVE. It never auto-links on name
similarity, never fuzzy-merges, never deletes/merges companies, and never infers
a company identity from participant ordering.

Match status vocabulary (explicit, never a bare ``CompanyProfile``):
  EXACT_NPWP                - exact normalized NPWP match to ONE company (auto-link OK)
  EXACT_OFFICIAL_IDENTIFIER - exact match on another verified official unique
                              identifier (NIB) to ONE company (auto-link OK)
  CANDIDATE_ONLY            - a company shares the name but identity is NOT proven
                              (never auto-linked)
  IDENTITY_CONFLICT         - multiple companies share the looked-up identifier, or a
                              same-name/different-identifier ambiguity; never auto-linked
  UNRESOLVED                - no usable identity and/or no candidate found

NPWP normalization is DETERMINISTIC and distinct from fiscal validation:
  - ``normalize_npwp`` only extracts digits and rejects masked / non-numeric values.
  - It does NOT claim the NPWP is fiscally valid (no checksum/validation algorithm).
  - Values that contain masking characters (``*``, ``x``, ``X``) — e.g. the SPSE
    masked ``00*5**7****42**0`` — are treated as UNUSABLE (never matched).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from loguru import logger

from spse_crawler.companies.models import CompanyProfile

if TYPE_CHECKING:
    pass


# Characters that indicate a masked / unusable NPWP (SPSE masks partly).
_MASK_CHARS = "*xX#"


def mask_identifier(raw: str | None) -> str:
    """Return a safe, non-sensitive representation of an identifier for logs.

    Only the first and last 2 digits are kept; the middle is replaced with
    ``****``. Empty/None becomes ``(empty)``. Used so full NPWP values are never
    dumped into logs.
    """
    if not raw:
        return "(empty)"
    s = str(raw).strip()
    if len(s) <= 4:
        return "****"
    return f"{s[:2]}****{s[-2:]}"


class ResolutionStatus(str, Enum):
    EXACT_NPWP = "EXACT_NPWP"
    EXACT_OFFICIAL_IDENTIFIER = "EXACT_OFFICIAL_IDENTIFIER"
    CANDIDATE_ONLY = "CANDIDATE_ONLY"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    UNRESOLVED = "UNRESOLVED"


# Only these statuses permit automatic linking.
AUTO_LINK_STATUSES = frozenset(
    {ResolutionStatus.EXACT_NPWP, ResolutionStatus.EXACT_OFFICIAL_IDENTIFIER}
)


def normalize_npwp(raw: str | None) -> str | None:
    """Deterministically normalize an NPWP to its digit-only form.

    Returns ``None`` (unusable) when the value is empty, contains alphabetic
    characters, or contains masking characters (e.g. ``00*5**7****42**0``).

    NOTE: this is normalization only, NOT fiscal validation. It never verifies
    that the NPWP is a valid Indonesian tax identifier.
    """
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    # Reject alphabetic content (an NPWP is numeric only; letters indicate noise).
    if any(ch.isalpha() for ch in raw):
        return None
    # Reject masked values (SPSE masks with '*'); a '?'/'x' mask is also unusable.
    if any(ch in _MASK_CHARS for ch in raw):
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    return digits


def _normalize_identifier(raw: str | None) -> str | None:
    """Normalize a generic official identifier (e.g. NIB) to a comparable form.

    Strips presentation-only separators (spaces, dashes, dots, slashes) and
    case-folds. This is canonicalization for comparison only.
    """
    if not raw:
        return None
    raw = str(raw).strip().lower()
    if not raw:
        return None
    cleaned = re.sub(r"[\s.\-/]+", "", raw)
    return cleaned or None


@dataclass(frozen=True)
class ResolutionResult:
    """Outcome of an entity-resolution attempt.

    ``status`` is always one of the explicit :class:`ResolutionStatus` values.
    ``company`` is populated ONLY for auto-linkable exact matches; otherwise
    ``None``. ``candidates`` lists matching ``CompanyProfile`` rows (never used
    for auto-link). ``normalized_npwp`` exposes what was actually matched on.
    """

    status: ResolutionStatus
    company: CompanyProfile | None
    candidates: tuple[CompanyProfile, ...] = ()
    reason: str = ""
    normalized_npwp: str | None = None


def apply_resolution_to(obj, result: ResolutionResult) -> bool:
    """Conservatively apply a resolution to a participant/winner instance.

    Sets ``company`` + ``resolution_status`` ONLY for auto-linkable exact
    matches. For non-exact outcomes it records ``resolution_status`` for
    provenance ONLY when the row is not already linked — it never clobbers an
    existing exact link, and never invents identity.

    Returns True when instance fields changed (caller should persist).
    """
    changed = False
    if result.status in AUTO_LINK_STATUSES and result.company is not None:
        if obj.company_id != result.company.id:
            obj.company = result.company
            changed = True
        if obj.resolution_status != result.status.value:
            obj.resolution_status = result.status.value
            changed = True
    elif getattr(obj, "company_id", None) is None:
        if obj.resolution_status != result.status.value:
            obj.resolution_status = result.status.value
            changed = True
    return changed


def _companies_by_normalized_npwp(normalized: str) -> list[CompanyProfile]:
    """Return CompanyProfiles whose normalized NPWP equals ``normalized``."""
    out = []
    for c in CompanyProfile.objects.all():
        if normalize_npwp(c.npwp) == normalized:
            out.append(c)
    return out


def _companies_by_normalized_nib(normalized_nib: str) -> list[CompanyProfile]:
    """Return CompanyProfiles whose normalized NIB equals ``normalized_nib``."""
    out = []
    for c in CompanyProfile.objects.all():
        if _normalize_identifier(c.nib) == normalized_nib:
            out.append(c)
    return out


def _companies_by_exact_name(name: str) -> list[CompanyProfile]:
    """Return CompanyProfiles whose name matches ``name`` exactly (case-insensitive)."""
    return list(CompanyProfile.objects.filter(name__iexact=name))


def resolve_company_identity(
    name: str | None = None,
    npwp: str | None = None,
    nib: str | None = None,
) -> ResolutionResult:
    """Resolve a source identity (participant/winner) to a CompanyProfile.

    Priority:
      1. EXACT_OFFICIAL_IDENTIFIER  (verified unique identifier, e.g. NIB)
      2. EXACT_NPWP                 (normalized NPWP, one match)
      3. CANDIDATE_ONLY             (name-only / NPWP not matched but name matches)
      4. IDENTITY_CONFLICT          (multiple companies share the looked-up key)
      5. UNRESOLVED                 (no usable identity / no candidate)

    Returns an explicit :class:`ResolutionResult`; never a bare CompanyProfile.
    """
    name = (name or "").strip()
    normalized_npwp = normalize_npwp(npwp)
    normalized_nib = _normalize_identifier(nib)

    # Priority 1: other verified official identifier (NIB).
    if normalized_nib:
        nib_matches = _companies_by_normalized_nib(normalized_nib)
        if len(nib_matches) == 1:
            logger.info(
                "ENTITY_AUTO_LINK company={} via NIB (id={})",
                nib_matches[0].name, nib_matches[0].id,
            )
            return ResolutionResult(
                status=ResolutionStatus.EXACT_OFFICIAL_IDENTIFIER,
                company=nib_matches[0],
                candidates=tuple(nib_matches),
                reason="exact NIB match (unique official identifier)",
            )
        if len(nib_matches) > 1:
            logger.warning(
                "ENTITY_ID_CONFLICT NIB matched {} companies (ids={}); no auto-link",
                len(nib_matches), [c.id for c in nib_matches],
            )
            return ResolutionResult(
                status=ResolutionStatus.IDENTITY_CONFLICT,
                company=None,
                candidates=tuple(nib_matches),
                reason="multiple companies share the same NIB; integrity conflict",
            )

    # Priority 2: exact NPWP.
    if normalized_npwp:
        npwp_matches = _companies_by_normalized_npwp(normalized_npwp)
        if len(npwp_matches) == 1:
            logger.info(
                "ENTITY_AUTO_LINK company={} (id={}) via NPWP masked={}",
                npwp_matches[0].name, npwp_matches[0].id,
                mask_identifier(npwp),
            )
            return ResolutionResult(
                status=ResolutionStatus.EXACT_NPWP,
                company=npwp_matches[0],
                candidates=tuple(npwp_matches),
                reason="exact normalized NPWP match",
                normalized_npwp=normalized_npwp,
            )
        if len(npwp_matches) > 1:
            logger.warning(
                "ENTITY_ID_CONFLICT NPWP masked={} matched {} companies (ids={}); no auto-link",
                mask_identifier(npwp), len(npwp_matches),
                [c.id for c in npwp_matches],
            )
            return ResolutionResult(
                status=ResolutionStatus.IDENTITY_CONFLICT,
                company=None,
                candidates=tuple(npwp_matches),
                reason="multiple companies share the same NPWP; integrity conflict (not auto-linked)",
                normalized_npwp=normalized_npwp,
            )

        # NPWP present but not matched: name may still offer a candidate (no auto-link).
        if name:
            name_cands = _companies_by_exact_name(name)
            if name_cands:
                logger.debug(
                    "ENTITY_CANDIDATE name={} — NPWP no-match; candidate ids={} (no auto-link)",
                    name, [c.id for c in name_cands],
                )
                return ResolutionResult(
                    status=ResolutionStatus.CANDIDATE_ONLY,
                    company=None,
                    candidates=tuple(name_cands),
                    reason="NPWP matched no company; name-only candidate (no auto-link)",
                    normalized_npwp=normalized_npwp,
                )
        logger.debug(
            "ENTITY_UNRESOLVED NPWP masked={} no company match, no name candidate",
            mask_identifier(npwp),
        )
        return ResolutionResult(
            status=ResolutionStatus.UNRESOLVED,
            company=None,
            reason="NPWP matched no CompanyProfile and no name candidate",
            normalized_npwp=normalized_npwp,
        )

    # No usable NPWP / NIB: name only.
    if name:
        name_cands = _companies_by_exact_name(name)
        if name_cands:
            logger.debug(
                "ENTITY_CANDIDATE name={} — candidate ids={} (no auto-link)",
                name, [c.id for c in name_cands],
            )
            return ResolutionResult(
                status=ResolutionStatus.CANDIDATE_ONLY,
                company=None,
                candidates=tuple(name_cands),
                reason="name-only candidate; identity not proven (no auto-link)",
            )
        logger.debug("ENTITY_UNRESOLVED name={} — no CompanyProfile matched", name)
        return ResolutionResult(
            status=ResolutionStatus.UNRESOLVED,
            company=None,
            reason="name provided but no CompanyProfile matched",
        )

    logger.debug("ENTITY_UNRESOLVED no usable identity provided")
    return ResolutionResult(
        status=ResolutionStatus.UNRESOLVED,
        company=None,
        reason="no usable identity provided (empty name and NPWP)",
    )

"""Audit trail service — create audit log entries for tracked actions."""

from loguru import logger


def get_client_ip(request) -> str | None:
    """Extract client IP from request, accounting for X-Forwarded-For proxy header."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_status_change(user, tender, company, old_status: str, new_status: str, request=None) -> None:
    """Log a submission status change."""
    from .models import AuditLog

    try:
        AuditLog.objects.create(
            user=user,
            company=company,
            action="status_change",
            target_type="TenderSubmissionStatus",
            target_id=tender.id,
            old_value=old_status,
            new_value=new_status,
            ip_address=get_client_ip(request) if request else None,
        )
    except Exception as exc:
        logger.error("[AUDIT] Failed to log status change: {}", exc)


def log_qualification_edit(user, company, qual_id: int, old_value: str, new_value: str, request=None) -> None:
    """Log a qualification data edit."""
    from .models import AuditLog

    try:
        AuditLog.objects.create(
            user=user,
            company=company,
            action="qualification_edit",
            target_type="CompanyQualification",
            target_id=qual_id,
            old_value=old_value,
            new_value=new_value,
            ip_address=get_client_ip(request) if request else None,
        )
    except Exception as exc:
        logger.error("[AUDIT] Failed to log qualification edit: {}", exc)


def log_match_trigger(user, company, tender_id: int, request=None) -> None:
    """Log an AI match trigger."""
    from .models import AuditLog

    try:
        AuditLog.objects.create(
            user=user,
            company=company,
            action="match_trigger",
            target_type="AIMatchResult",
            target_id=tender_id,
            ip_address=get_client_ip(request) if request else None,
        )
    except Exception as exc:
        logger.error("[AUDIT] Failed to log match trigger: {}", exc)

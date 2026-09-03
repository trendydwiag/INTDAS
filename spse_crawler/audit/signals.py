"""Django signals for automatic audit logging on TenderSubmissionStatus changes."""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from spse_crawler.submissions.models import TenderSubmissionStatus


@receiver(pre_save, sender=TenderSubmissionStatus)
def _capture_old_status(sender, instance, **kwargs):
    """Store old status before save for comparison in post_save."""
    if instance.pk:
        try:
            old = TenderSubmissionStatus.objects.get(pk=instance.pk)
            instance._old_status = old.status
        except TenderSubmissionStatus.DoesNotExist:
            instance._old_status = ""
    else:
        instance._old_status = ""


@receiver(post_save, sender=TenderSubmissionStatus)
def _log_status_change(sender, instance, created, **kwargs):
    """Auto-log status changes to AuditLog."""
    from spse_crawler.audit.service import log_status_change

    old_status = getattr(instance, "_old_status", "")
    new_status = instance.status

    # Only log actual changes
    if old_status == new_status:
        return

    log_status_change(
        user=instance.updated_by,
        tender=instance.tender,
        company=instance.company,
        old_status=old_status,
        new_status=new_status,
    )

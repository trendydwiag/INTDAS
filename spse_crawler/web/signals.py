from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone as dj_timezone

from .models import TenderResult


@receiver(pre_save, sender=TenderResult)
def set_tanggal_dibuat(sender, instance, **kwargs):
    """Set tanggal_dibuat to scraped_at on first creation only."""
    if instance.pk is None and not instance.tanggal_dibuat:
        instance.tanggal_dibuat = instance.scraped_at or dj_timezone.now()

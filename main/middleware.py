"""
main/middleware.py — Custom Django middleware for HR activity tracking.

HOW TO MODIFY
-------------
  Exclude additional paths from logging  → extend the startswith() check below.
  Log additional event types             → add branches in __call__ before or after
                                            get_response().
  Change middleware order                → edit MIDDLEWARE list in smscrweb/settings.py.
  Rename this class                      → update the dotted path in settings.py MIDDLEWARE.
  Change log retention                   → edit _ACTIVITY_LOG_KEEP_DAYS below.

IMPORTANT: Login/logout events are logged separately via signals in models.py.
This middleware only logs PAGE_VIEW events.

PRUNING: On ~2% of staff page-view requests, entries older than _ACTIVITY_LOG_KEEP_DAYS
days are deleted. This keeps the HRActivityLog table from growing without bound without
requiring a scheduled task.
"""

import random
from datetime import timedelta

from django.utils import timezone

from .models import HRActivityLog

_ACTIVITY_LOG_KEEP_DAYS = 30  # retain this many days of page-view history


class HRActivityMiddleware:  # Class name must match the path in settings.MIDDLEWARE # <--- This name must match settings.py
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated and request.user.is_staff:
            if not request.path.startswith(('/static/', '/media/', '/admin/jsi18n/')):
                HRActivityLog.objects.create(
                    user=request.user,
                    event_type="PAGE_VIEW",
                    path=request.path
                )
                # Probabilistic pruning: ~2% of requests trigger a cleanup so the
                # table stays bounded without a dedicated scheduled task.
                if random.random() < 0.02:
                    cutoff = timezone.now() - timedelta(days=_ACTIVITY_LOG_KEEP_DAYS)
                    HRActivityLog.objects.filter(timestamp__lt=cutoff).delete()
        return response
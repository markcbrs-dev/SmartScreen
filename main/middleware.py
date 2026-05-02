"""
main/middleware.py — Custom Django middleware for HR activity tracking.

HOW TO MODIFY
-------------
  Exclude additional paths from logging  → extend the startswith() check below.
  Log additional event types             → add branches in __call__ before or after
                                            get_response().
  Change middleware order                → edit MIDDLEWARE list in smscrweb/settings.py.
  Rename this class                      → update the dotted path in settings.py MIDDLEWARE.

IMPORTANT: Login/logout events are logged separately via signals in models.py.
This middleware only logs PAGE_VIEW events.
"""

from .models import HRActivityLog

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
        return response
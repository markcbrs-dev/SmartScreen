from .models import HRActivityLog

class HRActivityMiddleware: # <--- This name must match settings.py
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
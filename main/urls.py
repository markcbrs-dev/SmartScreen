from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name='apply', permanent=False), name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("apply/", views.apply, name="apply"),
    path("register/", views.hr_register, name="hr_register"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("audit-log/", views.audit_log_view, name="audit_log"),
]
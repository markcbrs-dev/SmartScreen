from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name='login', permanent=False), name="home"),
    path("login/", views.login_view, name="login"),
    path("apply/", views.apply, name="apply"),
    path("hr/register/", views.hr_register, name="hr_register"),
    path("dashboard/", views.dashboard, name="dashboard"),
]
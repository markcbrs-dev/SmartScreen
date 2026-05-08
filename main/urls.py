"""
main/urls.py — URL routing for the main app.

HOW TO MODIFY
-------------
  Add a new page  → append a new path() entry here and create the view in views.py.
  Rename a URL    → change the string in path(); update any reverse('name') calls in
                     views.py and {% url 'name' %} tags in templates.
  Restrict access → apply @_staff_required decorator to the view in views.py.

CURRENT ROUTES
--------------
  /                        → role-selection home page
  /login/                  → HR login form
  /logout/                 → HR logout (POST only)
  /apply/                  → public applicant submission form
  /register/               → HR self-registration (account inactive until admin approves)
  /dashboard/              → HR dashboard (job upload + applicant scoring)
  /audit-log/              → recent applicant history entries
  /resumes/<filename>      → staff-only authenticated resume download (protects PII)
"""

from django.urls import path
from . import views

urlpatterns = [
    path("", views.home_view, name="home"),
    path("home/", views.home_view, name="home_alias"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("apply/", views.apply, name="apply"),
    path("register/", views.hr_register, name="hr_register"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("audit-log/", views.audit_log_view, name="audit_log"),
    path("resumes/<str:filename>", views.serve_resume, name="serve_resume"),
]
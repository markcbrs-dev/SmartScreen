"""
main/models.py — Database models for SmartScreen.

OVERVIEW
--------
This file defines all database tables (models) used by the application.

HOW TO MODIFY
-------------
- Add a new field       → add it to the relevant model class, then run:
                          manage.py makemigrations && manage.py migrate
- Change status options → edit Applicant.STATUS_CHOICES
- Add a new model       → define it here, register in admin.py, make + run migrations
- Change audit logging  → edit the @receiver functions or the Applicant/JobDescription.save()
                          override (search "# Safety check" and "# Attribute the change")
- Add history to a model → add `history = HistoricalRecords()` and migrate

MODELS IN THIS FILE
-------------------
  HRActivityLog   — tracks login, logout, and page views for HR staff
  Resume          — LEGACY / UNUSED; kept to avoid breaking migration history
  Details         — LEGACY / UNUSED; detail lines for Resume
  Applicant       — core applicant record (resume upload, parsed data, match score)
  JobDescription  — uploaded job description with parsed structured fields
  AdminUser       — proxy of User (superusers) for cleaner admin display
  HRUser          — proxy of User (staff, non-superuser) for cleaner admin display
"""

from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from simple_history.models import HistoricalRecords

from .security import EncryptedJSONField, EncryptedResumeFileField, EncryptedTextField

# --- MONITORING MODEL ---

class HRActivityLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=50) # e.g., "LOGIN", "LOGOUT"
    path = models.CharField(max_length=255)      # The URL accessed
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "HR Activity Logs"
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user.username} - {self.event_type} - {self.timestamp}"

# --- SIGNALS FOR LOGIN/LOGOUT ---

@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    # Only log if the user is staff (HR or Admin)
    if user.is_staff:
        HRActivityLog.objects.create(user=user, event_type="LOGIN", path=request.path)

@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    if user.is_staff:
        HRActivityLog.objects.create(user=user, event_type="LOGOUT", path=request.path)


# --- DATA MODELS ---

# -----------------------------------------------------------------------
# LEGACY MODELS — not used by any view or service.
# Resume + Details were from the original prototype. They are kept here
# only to preserve migration history (0001_initial.py depends on them).
# Do NOT add business logic here. Safe to leave as-is.
# -----------------------------------------------------------------------
class Resume(models.Model):
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Details(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE)
    text = models.CharField(max_length=300)
    complete = models.BooleanField()

    class Meta:
        verbose_name_plural = "Details"


class Applicant(models.Model):
    # MODIFY HERE: add or rename applicant pipeline stages
    STATUS_CHOICES = [
        ('NEW', 'New'),
        ('INTERVIEW', 'For Interview'),
    ]

    first_name = EncryptedTextField(default='')
    last_name = EncryptedTextField(default='')
    contact_number = EncryptedTextField(default='')
    email = EncryptedTextField(default='')
    resume = EncryptedResumeFileField(upload_to='resumes/')
    applied_job = models.ForeignKey('JobDescription', null=True, blank=True, on_delete=models.SET_NULL, related_name='applicants')
    job_preference_locked = models.BooleanField(default=False)
    position = models.CharField(max_length=50, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NEW')
    match_score = models.IntegerField(default=0)  # 0–100; recalculated by recruitment.refresh_applicant_match
    resume_text = EncryptedTextField(blank=True, default='')   # raw extracted text from uploaded resume
    parsed_skills = EncryptedJSONField(default=list, blank=True)     # list[str] — populated by ResumeParser
    parsed_education = EncryptedJSONField(default=list, blank=True)  # list[str] — populated by ResumeParser
    parsed_experience = EncryptedJSONField(default=list, blank=True) # list[str] — populated by ResumeParser
    parsed_locations = EncryptedJSONField(default=list, blank=True)  # list[str] — populated by ResumeParser
    match_notes = EncryptedJSONField(default=dict, blank=True)  # full breakdown from JobMatchResult; see services/matcher.py
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    def save(self, *args, **kwargs):
        # 1. Attribute the change to the Job Uploader
        if self.applied_job:
            # We look for the first history entry (creation) of the job description
            # to find the original uploader
            job_uploader_entry = self.applied_job.history.order_by('history_date').first()
            
            if job_uploader_entry and job_uploader_entry.history_user:
                # Set the history user for this applicant to the job uploader
                self._history_user = job_uploader_entry.history_user

        # 2. Safety check: Disable history logging if the person isn't staff
        if hasattr(self, '_history_user') and self._history_user:
            if not self._history_user.is_staff:
                self.skip_history_when_saving = True
        
        super(Applicant, self).save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name} <{self.email}>"


# -----------------------------------------------------------------------
# JobDescription — one row per uploaded job description file.
# Parsed fields are populated by JobDescriptionParser (services/job_parser.py)
# and saved via recruitment.replace_active_job_description().
# To add a new structured field: add the model field here, add parsing logic
# in job_parser.py, and update update_job_from_profile() in recruitment.py.
# -----------------------------------------------------------------------
class JobDescription(models.Model):
    title = models.CharField(max_length=200)
    description_file = models.FileField(upload_to='jobs/')
    reporting_to = models.CharField(max_length=200, blank=True, default='')
    company = models.CharField(max_length=200, blank=True, default='')
    location = models.CharField(max_length=200, blank=True, default='')
    employment_type = models.CharField(max_length=100, blank=True, default='')
    experience_level = models.CharField(max_length=100, blank=True, default='')
    salary_range = models.CharField(max_length=100, blank=True, default='')
    remote_option = models.BooleanField(default=False)
    required_skills = models.JSONField(default=list, blank=True)
    preferred_skills = models.JSONField(default=list, blank=True)
    responsibilities = models.JSONField(default=list, blank=True)
    qualifications = models.JSONField(default=list, blank=True)
    education_requirements = models.JSONField(default=list, blank=True)
    certifications = models.JSONField(default=list, blank=True)
    benefits = models.JSONField(default=list, blank=True)
    industries = models.JSONField(default=list, blank=True)
    raw_text = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    def save(self, *args, **kwargs):
        # Skip history if user is not staff
        if hasattr(self, '_history_user') and self._history_user:
            if not self._history_user.is_staff:
                self.skip_history_when_saving = True
        super(JobDescription, self).save(*args, **kwargs)

    def __str__(self):
        return self.title

# --- PROXY MODELS ---

class AdminUser(User):
    class Meta:
        proxy = True
        verbose_name = 'Admin Account'
        verbose_name_plural = 'Admin Accounts'

class HRUser(User):
    class Meta:
        proxy = True
        verbose_name = 'HR Account'
        verbose_name_plural = 'HR Accounts'
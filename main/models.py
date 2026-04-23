from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from simple_history.models import HistoricalRecords

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
    STATUS_CHOICES = [
        ('NEW', 'New'),
        ('REVIEWING', 'Reviewing'),
        ('INTERVIEW', 'Interview'),
        ('OFFER', 'Offer'),
    ]

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    contact_number = models.CharField(max_length=50)
    email = models.EmailField()
    resume = models.FileField(upload_to='resumes/')
    applied_job = models.ForeignKey('JobDescription', null=True, blank=True, on_delete=models.SET_NULL, related_name='applicants')
    job_preference_locked = models.BooleanField(default=False)
    position = models.CharField(max_length=100, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NEW')
    match_score = models.IntegerField(default=0)
    resume_text = models.TextField(blank=True, default='')
    parsed_skills = models.JSONField(default=list, blank=True)
    parsed_education = models.JSONField(default=list, blank=True)
    parsed_experience = models.JSONField(default=list, blank=True)
    parsed_locations = models.JSONField(default=list, blank=True)
    match_notes = models.JSONField(default=dict, blank=True)
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


class JobDescription(models.Model):
    title = models.CharField(max_length=200)
    description_file = models.FileField(upload_to='jobs/')
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
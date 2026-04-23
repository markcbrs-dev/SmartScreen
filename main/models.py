from django.db import models
from simple_history.models import HistoricalRecords

# Create your models here.
class Resume(models.Model):
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Details(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE)
    text = models.CharField(max_length=300)
    complete = models.BooleanField()

    class Meta:
        # This fixes the "Detailss" spelling in the admin panel
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

    # This line tracks every change made to an applicant for the Audit Log
    history = HistoricalRecords()

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

    # This line tracks every change made to a Job for the Audit Log
    history = HistoricalRecords()

    def __str__(self):
        return self.title
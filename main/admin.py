from django.contrib import admin
from .models import Resume, Details, Applicant, JobDescription

# Register your models here.
admin.site.register(Resume)
admin.site.register(Details)
admin.site.register(Applicant)
admin.site.register(JobDescription)

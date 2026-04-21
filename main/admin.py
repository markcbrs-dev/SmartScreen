from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from .models import Resume, Details, Applicant, JobDescription

@admin.action(description='Approve selected HR accounts (Set Active)')
def approve_hr(modeladmin, request, queryset):
    queryset.update(is_active=True)

@admin.action(description='Deny/Deactivate selected HR accounts (Set Inactive)')
def deny_hr(modeladmin, request, queryset):
    queryset.update(is_active=False)

admin.site.unregister(User)

class CustomUserAdmin(UserAdmin):
    actions = [approve_hr, deny_hr]
    
   
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'is_active')

admin.site.register(User, CustomUserAdmin)

# --- Your Existing Model Registrations ---

admin.site.register(Resume)
admin.site.register(Details)
admin.site.register(Applicant)
admin.site.register(JobDescription)
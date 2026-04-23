from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from simple_history.admin import SimpleHistoryAdmin 
from .models import Resume, Details, Applicant, JobDescription

# --- HR Approval System ---
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


# --- Audit Log System ---

# 1. Admin for Applicants (Includes Status)
class ApplicantAdmin(SimpleHistoryAdmin):
    list_display = ('id', '__str__', 'status', 'modified_by', 'created_at')
    history_list_display = ('status', 'history_user', 'history_type')

    def modified_by(self, obj):
        latest_history = obj.history.first()
        if latest_history and latest_history.history_user:
            return latest_history.history_user.get_full_name() or latest_history.history_user.username
        return "System"
    modified_by.short_description = 'Last Changed By'

# 2. Admin for Job Descriptions (No Status field exists here)
class JobDescriptionAdmin(SimpleHistoryAdmin):
    list_display = ('id', 'title', 'modified_by', 'created_at')
    history_list_display = ('title', 'history_user', 'history_type')

    def modified_by(self, obj):
        latest_history = obj.history.first()
        if latest_history and latest_history.history_user:
            return latest_history.history_user.get_full_name() or latest_history.history_user.username
        return "System"
    modified_by.short_description = 'Last Changed By'

# Register with their specific admin classes
admin.site.register(Applicant, ApplicantAdmin)
admin.site.register(JobDescription, JobDescriptionAdmin)

# Standard registration
admin.site.register(Resume)
admin.site.register(Details)
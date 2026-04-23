from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.urls import reverse
from django.utils.html import format_html
from simple_history.admin import SimpleHistoryAdmin 
from .models import (
    Resume, Details, Applicant, JobDescription,
    AdminUser, HRUser, HRActivityLog 
)

# --- HR Approval Actions ---
@admin.action(description='Approve selected accounts (Set Active)')
def approve_hr(modeladmin, request, queryset):
    queryset.update(is_active=True)

@admin.action(description='Deny/Deactivate selected accounts (Set Inactive)')
def deny_hr(modeladmin, request, queryset):
    queryset.update(is_active=False)

# Unregister default User Admin
admin.site.unregister(User)

# --- 1. ADMIN USERS SECTION ---
@admin.register(AdminUser)
class CustomAdminUserAdmin(UserAdmin):
    actions = [approve_hr, deny_hr]
    list_display = ('username', 'email', 'is_staff', 'is_active')
    
    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_superuser=True)

# --- 2. HR USERS SECTION ---
@admin.register(HRUser)
class CustomHRUserAdmin(UserAdmin):
    actions = [approve_hr, deny_hr]
    list_display = ('username', 'email', 'is_staff', 'is_active', 'view_activity_link')

    def view_activity_link(self, obj):
        # This button link still works even when the tab is hidden
        url = reverse('admin:main_hractivitylog_changelist') + f'?user__id__exact={obj.id}'
        return format_html(
            '<a class="button" style="background-color: #417690; color: white; padding: 3px 10px; border-radius: 4px;" href="{}">History</a>', 
            url
        )
    
    view_activity_link.short_description = 'HR Monitoring'

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_staff=True, is_superuser=False)

# --- 3. HR ACTIVITY LOGS (STRICTLY HIDDEN FROM SIDEBAR) ---
class HRActivityLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'event_type', 'path')
    list_filter = ('user', 'event_type', 'timestamp')
    readonly_fields = ('user', 'event_type', 'path', 'timestamp')

    # This prevents the model from appearing in the Admin Sidebar and Dashboard index
    def has_module_permission(self, request):
        return False

# --- 4. DATA MODELS ---

class ApplicantAdmin(SimpleHistoryAdmin):
    # Updated to reflect the Position Handler (Job Uploader)
    list_display = ('id', '__str__', 'status', 'modified_by', 'created_at')
    history_list_display = ('status', 'history_user', 'history_type')

    def has_view_history_permission(self, request, obj=None):
        return False

    def modified_by(self, obj):
        # Logic: Show the email of the person who originally uploaded the associated job
        latest_history = obj.history.first()
        if latest_history and latest_history.history_user:
            user = latest_history.history_user
            return user.email if user.email else user.username
        return "System"
    
    modified_by.short_description = 'Last Changed By'

class JobDescriptionAdmin(SimpleHistoryAdmin):
    list_display = ('id', 'title', 'modified_by', 'created_at')
    history_list_display = ('title', 'history_user', 'history_type')

    def modified_by(self, obj):
        # Fetch the email from the simple-history record of the job description
        latest_history = obj.history.first()
        if latest_history and latest_history.history_user:
            user = latest_history.history_user
            return user.email if user.email else user.username
        return "System"
    
    modified_by.short_description = 'Uploader Email'

# --- FINAL REGISTRATIONS ---

# Registered manually so the URL remains active for the 'History' button.
admin.site.register(HRActivityLog, HRActivityLogAdmin)

admin.site.register(Applicant, ApplicantAdmin)
admin.site.register(JobDescription, JobDescriptionAdmin)
admin.site.register(Resume)
admin.site.register(Details)
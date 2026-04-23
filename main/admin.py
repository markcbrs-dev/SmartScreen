from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.urls import reverse
from django.utils.html import format_html
from simple_history.admin import SimpleHistoryAdmin 
from .models import Resume, Details, Applicant, JobDescription, HRActivityLog

# --- HR Approval System ---
@admin.action(description='Approve selected HR accounts (Set Active)')
def approve_hr(modeladmin, request, queryset):
    queryset.update(is_active=True)

@admin.action(description='Deny/Deactivate selected HR accounts (Set Inactive)')
def deny_hr(modeladmin, request, queryset):
    queryset.update(is_active=False)

# Unregister default User Admin
admin.site.unregister(User)

# Updated CustomUserAdmin with the "View History" button
class CustomUserAdmin(UserAdmin):
    actions = [approve_hr, deny_hr]
    # Added 'view_activity_link' to the list display
    list_display = ('username', 'email', 'is_staff', 'is_active', 'view_activity_link')

    def view_activity_link(self, obj):
        # This is the button to see what this specific HR person has been doing
        url = reverse('admin:main_hractivitylog_changelist') + f'?user__id__exact={obj.id}'
        return format_html('<a class="button" href="{}">History</a>', url)
    
    view_activity_link.short_description = 'HR Monitoring'

admin.site.register(User, CustomUserAdmin)


# --- HR Activity Log Admin ---
@admin.register(HRActivityLog)
class HRActivityLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'event_type', 'path')
    list_filter = ('user', 'event_type', 'timestamp')
    search_fields = ('user__username', 'path')
    readonly_fields = ('user', 'event_type', 'path', 'timestamp')


# --- Audit Log System ---

class ApplicantAdmin(SimpleHistoryAdmin):
    list_display = ('id', '__str__', 'status', 'modified_by', 'created_at')
    history_list_display = ('status', 'history_user', 'history_type')

    def modified_by(self, obj):
        latest_history = obj.history.first()
        if latest_history and latest_history.history_user:
            return latest_history.history_user.get_full_name() or latest_history.history_user.username
        return "System"
    modified_by.short_description = 'Last Changed By'

class JobDescriptionAdmin(SimpleHistoryAdmin):
    list_display = ('id', 'title', 'modified_by', 'created_at')
    history_list_display = ('title', 'history_user', 'history_type')

    def modified_by(self, obj):
        latest_history = obj.history.first()
        if latest_history and latest_history.history_user:
            return latest_history.history_user.get_full_name() or latest_history.history_user.username
        return "System"
    modified_by.short_description = 'Last Changed By'

# Register remaining models
admin.site.register(Applicant, ApplicantAdmin)
admin.site.register(JobDescription, JobDescriptionAdmin)
admin.site.register(Resume)
admin.site.register(Details)
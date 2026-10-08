from django.contrib import admin, messages

from .models import Profile
from .services import LinkError, approve_link, reject_link


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "transifex_username", "link_status"]
    list_filter = ["link_status"]
    search_fields = ["user__username", "transifex_username"]
    actions = ["approve", "reject"]

    @admin.action(description="Aprovar vínculo com o Transifex")
    def approve(self, request, queryset):
        for profile in queryset:
            try:
                claimed = approve_link(profile)
                self.message_user(request, f"{profile.user}: aprovado ({claimed} eventos creditados).")
            except LinkError as exc:
                self.message_user(request, f"{profile.user}: {exc}", level=messages.ERROR)

    @admin.action(description="Recusar vínculo")
    def reject(self, request, queryset):
        for profile in queryset:
            reject_link(profile)

from django.contrib import admin

from voice.models import VoiceSession


@admin.register(VoiceSession)
class VoiceSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "state", "booking_reference", "created_at", "closed_at")
    list_filter = ("status", "state")
    readonly_fields = ("id", "created_at", "updated_at", "closed_at")
    search_fields = ("booking_reference",)

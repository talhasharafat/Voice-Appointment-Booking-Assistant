from django.contrib import admin

from booking.models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    """Staff-only clinic records. Anonymous users cannot reach this screen."""

    list_display = (
        "booking_reference",
        "patient",
        "doctor",
        "starts_at",
        "ends_at",
        "status",
    )
    list_filter = ("status", "doctor", "starts_at")
    search_fields = (
        "booking_reference",
        "patient__name",
        "patient__contact_number",
        "reason",
        "idempotency_key",
    )
    autocomplete_fields = ("patient", "doctor")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "starts_at"

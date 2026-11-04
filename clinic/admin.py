from django.contrib import admin

from clinic.models import Doctor, Patient, Specialty, Unavailability, WorkingHours


class WorkingHoursInline(admin.TabularInline):
    model = WorkingHours
    extra = 0


class UnavailabilityInline(admin.TabularInline):
    model = Unavailability
    extra = 0


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name", "description")


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("name", "specialty", "is_active")
    list_filter = ("specialty", "is_active")
    search_fields = ("name",)
    inlines = (WorkingHoursInline, UnavailabilityInline)


@admin.register(WorkingHours)
class WorkingHoursAdmin(admin.ModelAdmin):
    list_display = ("doctor", "weekday", "start_time", "end_time")
    list_filter = ("weekday", "doctor")


@admin.register(Unavailability)
class UnavailabilityAdmin(admin.ModelAdmin):
    list_display = ("doctor", "starts_at", "ends_at", "reason")
    list_filter = ("doctor",)


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    """Staff-only. Django admin already requires is_staff plus model permissions."""

    list_display = ("name", "contact_number")
    search_fields = ("name", "contact_number")

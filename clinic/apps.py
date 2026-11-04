from django.apps import AppConfig


class ClinicConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "clinic"
    verbose_name = "Clinic"

    def ready(self):
        from django.contrib import admin

        admin.site.site_header = "Clinic staff"
        admin.site.site_title = "Clinic admin"
        admin.site.index_title = "Doctors, patients, and appointments"

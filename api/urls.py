from django.urls import path

from api import views

app_name = "api"

urlpatterns = [
    path("clinic-config/", views.clinic_config, name="clinic_config"),
    path("specialties/", views.specialties, name="specialties"),
    path("doctors/", views.doctors, name="doctors"),
    path("availability/", views.availability, name="availability"),
    path("appointments/", views.create_appointment, name="appointments"),
]

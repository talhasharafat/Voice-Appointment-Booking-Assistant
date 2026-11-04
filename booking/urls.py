from django.urls import path

from booking import views

app_name = "booking"

urlpatterns = [
    path("api/specialties/", views.specialties, name="specialties"),
    path("api/doctors/", views.doctors, name="doctors"),
    path("api/availability/", views.availability, name="availability"),
    path("api/appointments/", views.create_appointment, name="create_appointment"),
]

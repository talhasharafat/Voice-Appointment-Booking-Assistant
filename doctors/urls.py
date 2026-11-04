from django.urls import path

from doctors import views

app_name = "doctors"

urlpatterns = [
    path("", views.doctor_list, name="list"),
    path("<int:doctor_id>/", views.doctor_detail, name="detail"),
]

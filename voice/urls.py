from django.urls import path

from voice import views

app_name = "voice"

urlpatterns = [
    path("", views.assistant, name="assistant"),
    path("api/health/", views.health, name="health"),
    path("api/livekit/token/", views.livekit_token, name="livekit_token"),
    path("api/session/start/", views.start, name="start"),
    path("api/session/turn/", views.turn, name="turn"),
    path("api/session/abandon/", views.abandon, name="abandon"),
]

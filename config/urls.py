from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="voice:assistant", permanent=False)),
    path("admin/", admin.site.urls),
    path("clinic/", include("clinic.urls")),
    path("booking/", include("booking.urls")),
    path("voice/", include("voice.urls")),
    path("ai/", include("ai.urls")),
]

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('regface.urls')),  # ✅ All regface views are routed here
]

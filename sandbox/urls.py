from django.apps import apps
from django.conf.urls import i18n
from django.conf.urls.static import static
from django.urls import include, path
from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

admin.autodiscover()

urlpatterns = [
    path("admin", admin.site.urls),
    path("i18n", include(i18n)),
    path("", include(apps.get_app_config("oscar").urls[0])),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

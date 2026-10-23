from django.apps import AppConfig
from django.db.models.signals import post_migrate


class LostfoundConfig(AppConfig):
    name = "lostfound"
    verbose_name = "Campus Lost & Found"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from .roles import ensure_roles

        post_migrate.connect(ensure_roles, sender=self, dispatch_uid="lostfound_ensure_roles")

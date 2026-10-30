from django.apps import AppConfig
from django.db.models.signals import post_migrate


class LostfoundConfig(AppConfig):
    name = "lostfound"
    verbose_name = "Campus Lost & Found"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import checks  # noqa: F401  (registers the PostgreSQL startup check)
        from .roles import ensure_roles

        post_migrate.connect(ensure_roles, sender=self, dispatch_uid="lostfound_ensure_roles")

        from django.db.models.signals import post_delete, post_save

        from .models import Item, Location
        from .services.public import invalidate_board
        for model in (Item, Location):  # anything shown on the public board
            post_save.connect(invalidate_board, sender=model, dispatch_uid=f"board_cache_{model.__name__}_save")
            post_delete.connect(invalidate_board, sender=model, dispatch_uid=f"board_cache_{model.__name__}_delete")

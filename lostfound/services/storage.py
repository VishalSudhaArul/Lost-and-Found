import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """Files outside MEDIA_ROOT with no public URL. Served only through
    permission-checked views (lostfound.views.security.private_file).
    The location is read from settings on every access so it follows
    PRIVATE_MEDIA_ROOT (including test overrides)."""

    @property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError("Private files have no public URL.")


private_storage = PrivateStorage()


def private_storage_factory():
    return private_storage

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage


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


class _PrivateStorageProxy(Storage):
    """Chooses the private backend at use time: Cloudinary "authenticated" files when
    CLOUDINARY_URL is set (production), the local PRIVATE_MEDIA_ROOT otherwise.
    Neither backend can produce a public URL."""

    _cloud = None

    def _target(self):
        from .cloud_storage import PrivateCloudinaryStorage, cloudinary_enabled
        if cloudinary_enabled():
            if self._cloud is None:
                self._cloud = PrivateCloudinaryStorage()
            return self._cloud
        return private_storage

    def save(self, name, content, max_length=None):
        return self._target().save(name, content, max_length=max_length)

    def open(self, name, mode="rb"):
        return self._target().open(name, mode)

    def generate_filename(self, filename):
        return self._target().generate_filename(filename)

    def get_valid_name(self, name):
        return self._target().get_valid_name(name)

    def get_available_name(self, name, max_length=None):
        return self._target().get_available_name(name, max_length=max_length)

    def exists(self, name):
        return self._target().exists(name)

    def delete(self, name):
        return self._target().delete(name)

    def size(self, name):
        return self._target().size(name)

    def path(self, name):
        return self._target().path(name)

    def listdir(self, path):
        return self._target().listdir(path)

    def url(self, name):
        raise ValueError("Private files have no public URL.")

    def deconstruct(self):
        return ("lostfound.services.storage.private_storage_factory", (), {})


_private_proxy = _PrivateStorageProxy()


def private_storage_factory():
    return _private_proxy

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.functional import LazyObject


class PrivateStorage(FileSystemStorage):
    """Files outside MEDIA_ROOT with no public URL. Served only through
    permission-checked views (lostfound.views.security.private_file)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
        super().__init__(**kwargs)

    def url(self, name):
        raise ValueError("Private files have no public URL.")


class _LazyPrivate(LazyObject):
    def _setup(self):
        self._wrapped = PrivateStorage()


private_storage = _LazyPrivate()


def private_storage_factory():
    return private_storage

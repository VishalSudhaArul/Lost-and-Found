"""Show a clear page (not a crash) if PostgreSQL becomes unreachable while the site is running."""
import logging

from django.db import InterfaceError, OperationalError
from django.shortcuts import render

logger = logging.getLogger("lostfound.db")


class DatabaseUnavailableMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        from .services.cloud_storage import MediaStorageError
        if isinstance(exception, MediaStorageError):
            return self._media_failure(request, exception)
        if not isinstance(exception, (OperationalError, InterfaceError)):
            return None
        import re
        from django.conf import settings
        first_line = str(exception).strip().splitlines()[0] if str(exception).strip() else type(exception).__name__
        # Redact any passwords or credentials before logging
        pw = settings.DATABASES.get("default", {}).get("PASSWORD")
        if pw and pw in first_line:
            first_line = first_line.replace(pw, "******")
        first_line = re.sub(r"://([^:]+):([^@]+)@", r"://\1:******@", first_line)
        first_line = re.sub(r"password=([^\s]+)", "password=******", first_line)
        logger.error("PostgreSQL unavailable while serving a request (%s: %s).", type(exception).__name__, first_line)
        return render(request, "errors/error.html", {
            "code": 503, "title": "The database is not available",
            "message": "Campus Lost & Found cannot reach its PostgreSQL database right now. Please try again in a "
                       "minute. Administrators: check that the PostgreSQL service is running."}, status=503)

    def _media_failure(self, request, exception):
        """Photo storage unavailable: the transaction around the save has rolled back, so nothing
        was recorded. Tell the user plainly instead of showing a server error."""
        from django.contrib import messages
        from django.shortcuts import redirect
        logger.error("Media storage unavailable while serving a request (%s).", exception)
        if request.method == "POST":
            messages.error(request, "Your photo couldn't be uploaded right now, so nothing was saved. "
                                    "Please check your connection and try again.")
            return redirect(request.get_full_path())
        return render(request, "errors/error.html", {
            "code": 503, "title": "This image is temporarily unavailable",
            "message": "The photo storage service could not be reached. Please try again in a minute."}, status=503)

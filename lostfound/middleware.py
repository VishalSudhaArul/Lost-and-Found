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

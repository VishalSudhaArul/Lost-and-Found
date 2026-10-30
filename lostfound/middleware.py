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
        # Log the type only: driver messages can include host and user names.
        logger.error("PostgreSQL unavailable while serving a request (%s).", type(exception).__name__)
        return render(request, "errors/error.html", {
            "code": 503, "title": "The database is not available",
            "message": "Campus Lost & Found cannot reach its PostgreSQL database right now. Please try again in a "
                       "minute. Administrators: check that the PostgreSQL service is running."}, status=503)

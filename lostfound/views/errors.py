from django.shortcuts import render


def bad_request(request, exception=None):
    return render(request, "errors/error.html", {"code": 400, "title": "That request couldn't be processed",
                                                  "message": "Check the address or form and try again."}, status=400)


def permission_denied(request, exception=None):
    return render(request, "errors/error.html", {"code": 403, "title": "You don't have access to this page",
                                                  "message": "This area is limited to staff with the right role. "
                                                             "If you think you should have access, contact the campus administrator."},
                  status=403)


def page_not_found(request, exception=None):
    return render(request, "errors/error.html", {"code": 404, "title": "Page not found",
                                                  "message": "The item or page may have been removed, or the link is wrong."},
                  status=404)


def server_error(request):
    return render(request, "errors/error.html", {"code": 500, "title": "Something went wrong on our side",
                                                  "message": "The error has been logged. Please try again shortly."}, status=500)

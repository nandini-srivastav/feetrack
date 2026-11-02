from django.shortcuts import render


def forbidden(request, exception=None):
    return render(request, "error.html", {"code": 403, "title": "You don't have access to this page",
                                          "text": "Your role or centre does not include this record."}, status=403)


def not_found(request, exception=None):
    return render(request, "error.html", {"code": 404, "title": "Page not found",
                                          "text": "The link may be old, or the record belongs to another centre."},
                  status=404)

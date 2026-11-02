"""Access control for staff pages.

Two rules, applied on every staff view:
  1. role: each view lists the roles allowed to open it;
  2. scope: accounts executives and counsellors only ever see students of
     their own centre. Querysets are filtered here, not in templates, so a
     guessed URL for another centre's student returns 404.
"""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from accounts.models import User
from fees.models import Student

ACCOUNTS = User.Role.ACCOUNTS
COUNSELLOR = User.Role.COUNSELLOR
DIRECTOR = User.Role.DIRECTOR
MONEY_ROLES = (ACCOUNTS, DIRECTOR)
ALL_STAFF = (ACCOUNTS, COUNSELLOR, DIRECTOR)


def staff_required(*roles):
    roles = roles or ALL_STAFF

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not (user.is_superuser or user.role in roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        wrapped.allowed_roles = roles
        return wrapped
    return decorator


def scope_students(user, qs=None):
    qs = qs if qs is not None else Student.objects.all()
    if user.sees_all_centres:
        return qs
    return qs.filter(batch__centre_id=user.centre_id)


def scope_by_centre(user, qs, field="student__batch__centre"):
    if user.sees_all_centres:
        return qs
    return qs.filter(**{f"{field}_id": user.centre_id})


def get_student(user, student_id):
    return get_object_or_404(scope_students(user).select_related("batch__centre", "plan", "parent"),
                             student_id=student_id)

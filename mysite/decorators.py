from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from functools import wraps


def user_has_role(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required(login_url='/login/')  # Redirects to login page if user is not authenticated
        def _wrapped_view(request, *args, **kwargs):
            if hasattr(request.user, 'role') and request.user.role in allowed_roles:
                return view_func(request, *args, **kwargs)
            return HttpResponseForbidden("You don't have permission to access this page.")
        return _wrapped_view
    return decorator

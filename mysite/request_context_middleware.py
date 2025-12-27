"""
Middleware to automatically set the current user in request context.

This middleware should be added to MIDDLEWARE in settings.py to enable
automatic user tracking for all requests.
"""

from mysite.request_context import set_current_user, clear_current_user


class RequestContextMiddleware:
    """
    Middleware that sets the current user in thread-local storage for each request.
    This allows models to track who created or updated them without explicitly
    passing the user to every save() call.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Set the current user before processing the request
        user = getattr(request, 'user', None)
        if user and getattr(user, "is_authenticated", False):
            set_current_user(user)
        else:
            set_current_user(None)

        try:
            # Process the request
            return self.get_response(request)
        finally:
            # Always clean up, even if view/middleware raises
            clear_current_user()



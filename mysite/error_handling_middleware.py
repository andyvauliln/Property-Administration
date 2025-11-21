from django.contrib import messages
from django.http import HttpResponseServerError
from mysite.telegram_logger import log_error
import logging

logger = logging.getLogger(__name__)


class GlobalErrorHandlingMiddleware:
    """
    Global error handling middleware that catches all unhandled exceptions
    and sends them to Telegram for monitoring
    """
    
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response

    def process_exception(self, request, exception):
        """
        Process any unhandled exception and send notification to Telegram
        """
        # Gather detailed user information
        user_info = self.get_user_info(request)
        
        # Gather request information
        additional_info = {
            'Method': request.method,
            'Path': request.path,
            'IP': self.get_client_ip(request),
        }
        
        # Add user details
        additional_info.update(user_info)
        
        # Add GET parameters if available
        if request.GET:
            get_params = {key: value for key, value in request.GET.items()}
            if get_params:
                additional_info['GET Params'] = str(get_params)[:200]
        
        # Add POST data if available (be careful with sensitive data)
        if request.method == 'POST' and request.POST:
            # Filter out sensitive fields
            safe_post_data = {
                key: value for key, value in request.POST.items() 
                if key.lower() not in ['password', 'token', 'secret', 'api_key', 'csrfmiddlewaretoken']
            }
            if safe_post_data:
                additional_info['POST Data'] = str(safe_post_data)[:200]
        
        # Create a context with user information
        user_context = user_info.get('Username', 'Anonymous')
        if user_info.get('User ID'):
            user_context += f" (ID: {user_info.get('User ID')})"
        
        # Log the error to Telegram
        log_error(
            error=exception,
            context=f"Web Request by {user_context}: {request.method} {request.path}",
            additional_info=additional_info
        )
        
        # Add the error message for the user
        messages.error(request, f"An error occurred: {str(exception)}")
        
        # Log to Django logger as well
        logger.error(
            f"Unhandled exception in {request.method} {request.path}",
            exc_info=True,
            extra={'request': request}
        )
        
        # Return None to let Django handle the response
        # or return a custom error response
        # return HttpResponseServerError("A server error occurred.")
        return None
    
    def get_client_ip(self, request):
        """Get the client's IP address from the request"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
    
    def get_user_info(self, request):
        """
        Extract detailed user information from the request
        """
        user_info = {}
        
        if hasattr(request, 'user') and request.user.is_authenticated:
            user = request.user
            
            # Basic user info
            user_info['Authenticated'] = 'Yes'
            user_info['Username'] = str(user.username) if hasattr(user, 'username') else str(user)
            user_info['User ID'] = str(user.id) if hasattr(user, 'id') else 'N/A'
            
            # Email
            if hasattr(user, 'email') and user.email:
                user_info['Email'] = user.email
            
            # Full name (if available)
            if hasattr(user, 'full_name') and user.full_name:
                user_info['Full Name'] = user.full_name
            elif hasattr(user, 'get_full_name'):
                full_name = user.get_full_name()
                if full_name:
                    user_info['Full Name'] = full_name
            
            # Role (if available in your User model)
            if hasattr(user, 'role') and user.role:
                user_info['Role'] = user.role
            
            # Phone (if available)
            if hasattr(user, 'phone') and user.phone:
                user_info['Phone'] = user.phone
            
            # Staff/Superuser status
            if hasattr(user, 'is_staff') and user.is_staff:
                user_info['Staff'] = 'Yes'
            if hasattr(user, 'is_superuser') and user.is_superuser:
                user_info['Superuser'] = 'Yes'
        else:
            user_info['Authenticated'] = 'No'
            user_info['Username'] = 'Anonymous'
        
        return user_info

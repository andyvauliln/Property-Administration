from django.contrib import messages
from django.http import HttpResponseServerError

class GlobalErrorHandlingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response

    def process_exception(self, request, exception):
        # Add the error message
        messages.error(request, f"An error occurred: {str(exception)}")
        
        # Optionally, you can return a custom error response
        # return HttpResponseServerError("A server error occurred.")

from django.contrib.auth import login
from django.shortcuts import render, redirect
from ..forms import CustomUserLoginForm
from django.urls import reverse_lazy
from django.contrib.auth.views import LogoutView


def custom_login_view(request):
    if request.method == 'POST':
        form = CustomUserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            # Check user role
            if user.role == 'Cleaner':
                return redirect('/cleanings')
            elif user.role in ['Admin', 'Manager']:
                return redirect('/')

            # Check if "Remember Me" was not ticked
            if not form.cleaned_data.get('remember_me'):
                # Set session to expire when user closes browser
                request.session.set_expiry(0)

            return redirect('/')
    else:
        form = CustomUserLoginForm()
    return render(request, 'login.html', {'form': form, "title": "login"})


class CustomLogoutView(LogoutView):
    # This will redirect to the URL pattern named 'login'
    next_page = reverse_lazy('login')

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        # Clear the session
        request.session.flush()
        return response

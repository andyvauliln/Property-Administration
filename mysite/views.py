from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.shortcuts import render, redirect
from .forms import CustomUserLoginForm
from django.urls import reverse_lazy
from django.contrib.auth.views import LogoutView
from .models import User
import logging
from mysite.forms import CustomUserForm
from django.contrib.auth.hashers import make_password

# logger = logging.getLogger(__name__)

@login_required
def index(request):
    context = {
        'current_user': request.user,
    }

    return render(request, 'index.html', context )

def users(request):
    users = User.objects.all()
    if request.method == 'POST':
        if 'add' in request.POST:
            # Handle adding a user
            form = CustomUserForm(request.POST)
            if form.is_valid():
                user = form.save(commit=False)
                user.password = make_password(request.POST['password'])
                user.save()
        elif 'edit' in request.POST:
            # Handle editing a user
            user = User.objects.get(id=request.POST['user_id'])
            form = CustomUserForm(request.POST, instance=user)
            if form.is_valid():
                user = form.save(commit=False)
                # Only change the password if a new one is provided
                if request.POST['password']:
                    user.password = make_password(request.POST['password'])
                user.save()
        elif 'delete' in request.POST:
            # Handle deleting a user
            user = User.objects.get(id=request.POST['user_id'])
            user.delete()
    else:
        form = CustomUserForm()

    return render(request, 'users.html', {'items': users, 'form': form})


def custom_login_view(request):
    if request.method == 'POST':
        form = CustomUserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            # Check if "Remember Me" was not ticked
            if not form.cleaned_data.get('remember_me'):
                # Set session to expire when user closes browser
                request.session.set_expiry(0)

            return redirect('/')
    else:
        form = CustomUserLoginForm()
    return render(request, 'login.html', {'form': form})

class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('login')  # This will redirect to the URL pattern named 'login'

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        # Clear the session
        request.session.flush()
        return response
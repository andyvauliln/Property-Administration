"""mysite URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from . import views


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.index, name='index'),
    path('login/', views.custom_login_view, name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    path('users/', views.users, name='users'),
    path('apartments/', views.apartments, name='apartments'),
    path('apartment/', views.apartment, name='apartment'),
    path('apartments_analytics/', views.apartments_analytics,
         name='apartments_analytics'),
    path('cleanings/', views.cleanings, name='cleanings'),
    path('bookings/', views.bookings, name='bookings'),
    # path('contracts/', views.contracts, name='contracts'),
    path('payments/', views.payments, name='payments'),
    path('notifications/', views.notifications, name='notifications'),
    path('paymentmethods/', views.payment_methods, name='paymentmethods'),
    path('paymenttypes/', views.payment_types, name='paymenttypes'),
    path('notifications/', views.notifications, name='notifications'),
    # path('docusign/', views.docusign, name='docusign'),
    # path('callback/', views.callback, name='callback'),
    path('payment-report/', views.paymentReport, name='paymentReport'),
    path('forward-message/', views.forward_message, name='forwardMessage'),
    path('forward-message2/', views.forward_message2, name='forwardMessage2'),
    path('generate-invoice/', views.generate_invoice, name='generateInvoice'),
    # path('telegram_webhook/', views.forward_telegram, name='telegram_webhook'),
]

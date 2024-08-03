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
    path('payments/', views.payments, name='payments'),
    path('notifications/', views.notifications, name='notifications'),
    path('paymentmethods/', views.payment_methods, name='paymentmethods'),
    path('paymenttypes/', views.payment_types, name='paymenttypes'),
    path('notifications/', views.notifications, name='notifications'),
    # path('docusign-callback/', views.docusign_callback, name='docusign_callback'),
    # path('adobesign-callback/', views.adobesign_callback, name='adobesign_callback'),
    path('docuseal-callback/', views.docuseal_callback, name='docuseal_callback'),
    path('payment-report/', views.paymentReport, name='paymentReport'),
    path('forward-message/', views.forward_message, name='forwardMessage'),
    path('generate-invoice/', views.generate_invoice, name='generateInvoice'),
    path('booking-report/', views.booking_report, name='booking_report'),
    path('payments-sync/', views.sync_payments, name='sync_payments'),
    path('booking-availability/', views.booking_availability, name='booking_availability'),
    # path('telegram_webhook/', views.forward_telegram, name='telegram_webhook'),
]


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
    path('apartmentprice/', views.apartment_prices, name='apartment_prices'),
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
    #path('conversation-webhook/', views.conversation_webhook, name='conversation_webhook'),
    path('conversation-created-webhook/', views.twilio_webhook, name='twilio_webhook'),
    # path('conversation-created-webhook-pre/', views.conversation_pre_webhook, name='twilio_webhook'),
    # path('conversation-pre-webhook/', views.conversation_pre_webhook, name='conversation_pre_webhook'),
    path('generate-invoice/', views.generate_invoice, name='generateInvoice'),
    path('booking-report/', views.booking_report, name='booking_report'),
    path('apartment-report/', views.apartment_report, name='apartment_report'),
    path('payments-sync/', views.sync_payments, name='sync_payments'),
    path('booking-availability/', views.booking_availability, name='booking_availability'),
    path('create-booking/', views.create_booking_by_link, name='create_booking_by_link'),
    path('handyman_calendar/', views.handyman_calendar, name='handyman_calendar'),
    path('parking_calendar/', views.parking_calendar, name='parking_calendar'),
    path('api/apartment-booking-dates/', views.ApartmentBookingDates.as_view(), name='apartment_booking_dates'),
    path('api/update-apartment-price-by-rooms/', views.UpdateApartmentPriceByRooms.as_view(), name='update_apartment_price_by_rooms'),
    path('api/update-single-apartment-price/', views.UpdateSingleApartmentPrice.as_view(), name='update_single_apartment_price'),
    # Chat interface URLs
    path('chat/', views.chat_list, name='chat_list'),
    path('chat/<str:conversation_sid>/', views.chat_detail, name='chat_detail'),
    path('chat/<str:conversation_sid>/send/', views.send_message, name='send_message'),
    path('chat/<str:conversation_sid>/load-more/', views.load_more_messages, name='load_more_messages'),

]


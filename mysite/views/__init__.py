from .apartment_calendar import apartment
from .apartments_report import apartments_analytics, apartment_report
from .dashboard import index
from .generate_invoice import generate_invoice
from .booking_report import booking_report
from .notifications import notifications
from .payments_report import paymentReport
from .messaging import twilio_webhook
from .login import CustomLogoutView, custom_login_view
from .generic_view import apartment_prices, bookings, cleanings, payment_methods, payment_types, payments, ai_management_view
from .ai_agent_views import ai_runs_view, ai_run_detail_view, ai_agent_message_status
from .apartments_view import apartments_view as apartments
from .users_view import users_view as users
from .payment_sync import sync_payments
from .payment_sync_v2 import sync_payments_v2, fetch_db_payments_for_matching, match_selection_v2, fetch_merged_db_payments_for_file
from .docuseal import docuseal_callback
from .booking_availability import booking_availability
from .one_link_contract import create_booking_by_link
from .handmade_calendar import handyman_calendar
from .parking_calendar import parking_calendar
from .booking_api import (
    ApartmentBookingDates,
    UpdateApartmentPriceByRooms,
    UpdateSingleApartmentPrice,
    RentalGuruCreateBookingAPI,
    RentalGuruUpdateBookingAPI,
    RentalGuruUpdateBookingBySourceIdAPI,
    RentalGuruCreatePaymentAPI,
    RentalGuruUpdatePaymentAPI,
)
from .calendar_notes import (
    create_calendar_note,
    list_calendar_notes,
    update_calendar_note,
    delete_calendar_note,
)
from .chat import (
    chat_list,
    export_group_chats_md,
    chat_detail,
    send_message,
    delete_all_chat_messages,
    delete_chat_conversation,
    update_chat_apartment_kb,
    update_chat_knowledge_base,
    generate_chat_knowledge_base,
    knowledge_base_prompt,
    update_conversation_notes,
    update_message_notes,
    generate_message_ai_answer,
    generate_all_customer_ai_answers_view,
    answer_rule_context,
    generate_answer_rule,
    save_answer_rule,
    kb_rule_context,
    generate_kb_rule,
    save_kb_rule,
    delete_chat_message,
    load_more_messages,
    chat_template_list,
    chat_template_create,
)
from .database_activity import database_activity
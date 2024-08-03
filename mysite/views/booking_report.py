
from django.shortcuts import redirect
from ..decorators import user_has_role
from mysite.models import Booking, Payment
import os
from django.utils import timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from django.db.models import Sum
from dateutil.relativedelta import relativedelta
import logging
from datetime import datetime

logger_common = logging.getLogger('mysite.common')


def print_info(message):
    print(message)
    logger_common.debug(message)


@user_has_role('Admin')
def booking_report(request):

    try:
        referer_url = request.META.get('HTTP_REFERER', '/')
        report_start_date = request.GET.get('report_start_date', None)
        report_end_date = request.GET.get('report_end_date', None)
        if (report_start_date and report_end_date):
            start_date = datetime.strptime(report_start_date, "%B %d %Y")
            end_date = datetime.strptime(report_end_date, "%B %d %Y")
            bookings = Booking.objects.filter(start_date__gte=start_date, end_date__lte=end_date)
            if (bookings):
                booking_report_url = generate_excel(bookings, report_start_date, report_end_date)
                print_info(f'Report created {booking_report_url}')

                return redirect(booking_report_url)
        return redirect(referer_url)
    except Exception as e:
        print_info(f"Error: Generating Invoice Error, {str(e)}")
        return redirect(referer_url)


def generate_excel(bookings, start_date, end_date):
    try:
        sheets_service, drive_service = get_google_sheets_service()
        print_info("Got GOOGLE Services")
        # Create a new spreadsheet
        spreadsheet_body = {
            'properties': {
                'title': f"Booking Report: {start_date.strftime('%B %d %Y')} - {end_date.strftime('%B %d %Y')}"
            },
            'sheets': [{
                'properties': {
                    'title': 'Booking Report'
                }
            }]
        }
        spreadsheet = sheets_service.create(body=spreadsheet_body).execute()
        spreadsheet_id = spreadsheet.get('spreadsheetId')
        print_info(f"Spredsheet created {spreadsheet_id}")
        data = prepare_data(bookings)

        # Insert summary data into the Summary sheet
        insert_report_data(sheets_service, spreadsheet_id, data)


        share_document_with_user(drive_service, spreadsheet_id)

        # Generate and return the link to the spreadsheet
        excel_link = f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit'
        return excel_link
    except Exception as e:
        print_info("ERROR: Error while generating Excel Report")
        return ""


def prepare_data(bookings: Booking):
    total_bookings = bookings.count()
    data = []
    
    total_animals = 0
    total_animals_cats = 0
    total_animals_dogs = 0
    total_animals_other = 0
    
    total_sources = 0
    total_sources_airbnb = 0
    total_sources_referal = 0
    total_sources_returning = 0
    total_sources_other = 0
    
    total_cars_rent = 0
    car_prices = []
    car_rent_days = []
    car_models = {}
    
    total_visit_purpose = 0
    total_visit_purpose_tourism = 0
    total_visit_purpose_work = 0
    total_visit_purpose_medical = 0
    total_visit_purpose_repair = 0
    total_visit_purpose_relocation = 0
    total_visit_purpose_other = 0
    
    for booking in bookings:
        row = { 
            "booking_id": booking.id,
            "apartment": booking.apartment.name, 
            "tenant": booking.tenant.full_name, 
            "tenant_number": booking.tenant.tenants_n, 
            "booking_days": (booking.end_date - booking.start_date).days, 
            "visit_purpose": booking.visit_purpose, 
            "is_car_rented": booking.is_rent_car,
            "car_price": booking.car_price, 
            "car_rent_days": booking.car_rent_days, 
            "car_model": booking.car_model,
            "animals" : booking.animals,
            "source" : booking.source,
        }
        data.append(row)
        
        # Animals
        if booking.animals:
            total_animals += 1
            if booking.animals == 'Cat':
                total_animals_cats += 1
            elif booking.animals == 'Dog':
                total_animals_dogs += 1
            else:
                total_animals_other += 1
        
        # Sources
        if booking.source:
            total_sources += 1
            if booking.source == 'Airbnb':
                total_sources_airbnb += 1
            elif booking.source == 'Referral':
                total_sources_referal += 1
            elif booking.source == 'Returning':
                total_sources_returning += 1
            else:
                total_sources_other += 1
        
        # Cars
        if booking.is_rent_car:
            total_cars_rent += 1
            car_prices.append(booking.car_price)
            car_rent_days.append(booking.car_rent_days)
            if booking.car_model in car_models:
                car_models[booking.car_model] += 1
            else:
                car_models[booking.car_model] = 1
        
        # Visit Purpose
        if booking.visit_purpose:
            total_visit_purpose += 1
            if booking.visit_purpose == 'Tourism':
                total_visit_purpose_tourism += 1
            elif booking.visit_purpose == 'Work Travel':
                total_visit_purpose_work += 1
            elif booking.visit_purpose == 'Medical':
                total_visit_purpose_medical += 1
            elif booking.visit_purpose == 'House Repair':
                total_visit_purpose_repair += 1
            elif booking.visit_purpose == 'Relocation':
                total_visit_purpose_relocation += 1
            else:
                total_visit_purpose_other += 1
    
    # Calculate car model statistics
    sorted_car_models = sorted(car_models.items(), key=lambda x: x[1], reverse=True)
    top_3_cars_model_name = [model[0] for model in sorted_car_models[:3]]
    top_3_cars_model_count = [model[1] for model in sorted_car_models[:3]]
    top_3_cars_model_percent = [(count / total_cars_rent) * 100 for count in top_3_cars_model_count]
    
    return {
        "data": data,
        # Total Bookings
        'total_bookings': total_bookings,
        # Animals
        'total_animals': total_animals,
        'total_animals_percent': (total_animals / total_bookings) * 100 if total_bookings else 0,
        'total_animals_cats': total_animals_cats,
        'total_animals_cats_percent': (total_animals_cats / total_animals) * 100 if total_animals else 0,
        'total_animals_dogs': total_animals_dogs,
        'total_animals_dogs_percent': (total_animals_dogs / total_animals) * 100 if total_animals else 0,
        'total_animals_other': total_animals_other,
        'total_animals_other_percent': (total_animals_other / total_animals) * 100 if total_animals else 0,
        # Sources
        'total_sources': total_sources,
        'total_sources_percent': (total_sources / total_bookings) * 100 if total_bookings else 0,
        'total_sources_airbnb': total_sources_airbnb,
        'total_sources_airbnb_percent': (total_sources_airbnb / total_sources) * 100 if total_sources else 0,
        'total_sources_referal': total_sources_referal,
        'total_sources_referal_percent': (total_sources_referal / total_sources) * 100 if total_sources else 0,
        'total_sources_returning': total_sources_returning,
        'total_sources_returning_percent': (total_sources_returning / total_sources) * 100 if total_sources else 0,
        'total_sources_other': total_sources_other,
        'total_sources_other_percent': (total_sources_other / total_sources) * 100 if total_sources else 0,
        # Cars
        'total_cars_rent': total_cars_rent,
        'total_cars_rent_percent': (total_cars_rent / total_bookings) * 100 if total_bookings else 0,
        'top_3_cars_model_name': top_3_cars_model_name,
        'top_3_cars_model_count': top_3_cars_model_count,
        'top_3_cars_model_percent': top_3_cars_model_percent,
        "min_car_rent_price": min(car_prices) if car_prices else 0,
        "max_car_rent_price": max(car_prices) if car_prices else 0,
        "avg_car_rent_price": sum(car_prices) / len(car_prices) if car_prices else 0,
        "min_car_rent_days": min(car_rent_days) if car_rent_days else 0,
        "max_car_rent_days": max(car_rent_days) if car_rent_days else 0,
        "avg_car_rent_days": sum(car_rent_days) / len(car_rent_days) if car_rent_days else 0,
        # Visit Purpose
        'total_visit_purpose': total_visit_purpose,
        'total_visit_purpose_percent': (total_visit_purpose / total_bookings) * 100 if total_bookings else 0,
        'total_visit_purpose_tourism': total_visit_purpose_tourism,
        'total_visit_purpose_tourism_percent': (total_visit_purpose_tourism / total_visit_purpose) * 100 if total_visit_purpose else 0,
        'total_visit_purpose_work': total_visit_purpose_work,
        'total_visit_purpose_work_percent': (total_visit_purpose_work / total_visit_purpose) * 100 if total_visit_purpose else 0,
        'total_visit_purpose_medical': total_visit_purpose_medical,
        'total_visit_purpose_medical_percent': (total_visit_purpose_medical / total_visit_purpose) * 100 if total_visit_purpose else 0,
        'total_visit_purpose_repair': total_visit_purpose_repair,
        'total_visit_purpose_repair_percent': (total_visit_purpose_repair / total_visit_purpose) * 100 if total_visit_purpose else 0,
        'total_visit_purpose_relocation': total_visit_purpose_relocation,
        'total_visit_purpose_relocation_percent': (total_visit_purpose_relocation / total_visit_purpose) * 100 if total_visit_purpose else 0,
        'total_visit_purpose_other': total_visit_purpose_other,
        'total_visit_purpose_other_percent': (total_visit_purpose_other / total_visit_purpose) * 100 if total_visit_purpose else 0,
    }


def insert_report_data(sheets_service, spreadsheet_id, data):
    # Prepare the report values
    report_values = [
        ["Total Bookings", data['total_bookings']],
        ["Total Animals", data['total_animals']],
        ["Total Animals Percent", data['total_animals_percent']],
        ["Total Cats", data['total_animals_cats']],
        ["Total Cats Percent", data['total_animals_cats_percent']],
        ["Total Dogs", data['total_animals_dogs']],
        ["Total Dogs Percent", data['total_animals_dogs_percent']],
        ["Total Other Animals", data['total_animals_other']],
        ["Total Other Animals Percent", data['total_animals_other_percent']],
        ["Total Sources", data['total_sources']],
        ["Total Sources Percent", data['total_sources_percent']],
        ["Total Airbnb", data['total_sources_airbnb']],
        ["Total Airbnb Percent", data['total_sources_airbnb_percent']],
        ["Total Referral", data['total_sources_referal']],
        ["Total Referral Percent", data['total_sources_referal_percent']],
        ["Total Returning", data['total_sources_returning']],
        ["Total Returning Percent", data['total_sources_returning_percent']],
        ["Total Other Sources", data['total_sources_other']],
        ["Total Other Sources Percent", data['total_sources_other_percent']],
        ["Total Cars Rented", data['total_cars_rent']],
        ["Total Cars Rented Percent", data['total_cars_rent_percent']],
        ["Top 3 Car Models", ", ".join(data['top_3_cars_model_name'])],
        ["Top 3 Car Models Count", ", ".join(map(str, data['top_3_cars_model_count']))],
        ["Top 3 Car Models Percent", ", ".join(map(str, data['top_3_cars_model_percent']))],
        ["Min Car Rent Price", data['min_car_rent_price']],
        ["Max Car Rent Price", data['max_car_rent_price']],
        ["Avg Car Rent Price", data['avg_car_rent_price']],
        ["Min Car Rent Days", data['min_car_rent_days']],
        ["Max Car Rent Days", data['max_car_rent_days']],
        ["Avg Car Rent Days", data['avg_car_rent_days']],
        ["Total Visit Purpose", data['total_visit_purpose']],
        ["Total Visit Purpose Percent", data['total_visit_purpose_percent']],
        ["Total Tourism", data['total_visit_purpose_tourism']],
        ["Total Tourism Percent", data['total_visit_purpose_tourism_percent']],
        ["Total Work Travel", data['total_visit_purpose_work']],
        ["Total Work Travel Percent", data['total_visit_purpose_work_percent']],
        ["Total Medical", data['total_visit_purpose_medical']],
        ["Total Medical Percent", data['total_visit_purpose_medical_percent']],
        ["Total House Repair", data['total_visit_purpose_repair']],
        ["Total House Repair Percent", data['total_visit_purpose_repair_percent']],
        ["Total Relocation", data['total_visit_purpose_relocation']],
        ["Total Relocation Percent", data['total_visit_purpose_relocation_percent']],
        ["Total Other Visit Purpose", data['total_visit_purpose_other']],
        ["Total Other Visit Purpose Percent", data['total_visit_purpose_other_percent']],
    ]

    # Add a blank row to separate statistics from the data rows
    report_values.append([])

    # Add column names for the row data
    report_values.append([
        "Booking ID", "Apartment", "Tenant", "Tenant Number", "Booking Days",
        "Visit Purpose", "Is Car Rented", "Car Price", "Car Rent Days",
        "Car Model", "Animals", "Source"
    ])

    # Add the data rows
    for row in data['data']:
        report_values.append([
            row['booking_id'],
            row['apartment'],
            row['tenant'],
            row['tenant_number'],
            row['booking_days'],
            row['visit_purpose'],
            row['is_car_rented'],
            row['car_price'],
            row['car_rent_days'],
            row['car_model'],
            row['animals'],
            row['source']
        ])

    # Define the range where the data will be inserted
    data_range = 'Sheet1!A1'

    # Update the spreadsheet with the report values
    sheets_service.values().update(
        spreadsheetId=spreadsheet_id, range=data_range,
        valueInputOption='USER_ENTERED', body={'values': report_values}).execute()

    print_info(f"Created Booking Report Sheet for {spreadsheet_id}")

def get_google_sheets_service():
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive']
    SERVICE_ACCOUNT_FILE = 'google_tokens.json'

    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)

    sheets_service = build('sheets', 'v4', credentials=credentials)
    drive_service = build('drive', 'v3', credentials=credentials)

    return sheets_service.spreadsheets(), drive_service


def share_document_with_user(service, document_id):

    try:
       # Permission for public read access
        public_permission = {
            'type': 'anyone',
            'role': 'reader',
        }
        service.permissions().create(
            fileId=document_id,
            body=public_permission,
            fields='id',
        ).execute()

        print_info(f"Document {document_id} shared to public")
    except Exception as e:
        print_info(f"Failed to share document: {e}")
# from docusign_esign import EnvelopesApi, EnvelopeDefinition, TemplateRole, Text, Tabs, EnvelopeEvent
# from docusign_esign import ApiClient, AuthenticationApi, RecipientViewRequest, EventNotification, RecipientEvent

def legacy():
    test = 3
    # def apartments_analytics(request):
    #     apartment_ids = request.GET.get('ids', "")  # 1,2,5,10
    #     apartment_type = request.GET.get('type', "")  # In Ownership, In Management
    #     rooms = request.GET.get('rooms', "")
    #     year = int(request.GET.get('year', date.today().year))
    #     apartments = Apartment.objects.all().order_by('name').values_list('id', 'name')
    #     apartments_len = len(apartments)

    #     start_date = date(year, 1, 1)
    #     today = date.today()
    #     year_range = list(range(2020, today.year + 3))

    #     end_date = start_date.replace(year=start_date.year + 1) - timedelta(days=1)
    #     queryset = Apartment.objects.all()

    #     # Apply filters based on the criteria
    #     if apartment_type:
    #         queryset = queryset.filter(apartment_type=apartment_type)

    #     if rooms:
    #         queryset = queryset.filter(bedrooms=rooms)

    #     if apartment_ids:
    #         if apartment_ids == '-1':
    #             # Show all apartments if ids is -1
    #             selected_apartments = queryset
    #         else:
    #             # If apartment_ids are specified, filter based on the list of IDs
    #             apartment_id_list = apartment_ids.split(',')
    #             queryset = queryset.filter(id__in=apartment_id_list)
    #             selected_apartments = queryset
    #     else:
    #         # Show no apartments if ids are not set
    #         selected_apartments = []

    #     # Getting bookings for the selected period
    #     bookings = Booking.objects.filter(
    #         Q(start_date__lte=end_date) & Q(end_date__gte=start_date)
    #     )

    #     # Getting payments for the selected period
    #     payments = Payment.objects.filter(
    #         payment_date__range=(start_date, end_date))

    #     apartments_data = {}
    #     apartments_month_data = []

    #     # calculate avg monthly and yearly income, outcome, profit, occupancy for all apartments
    #     year_occupancy = 0
    #     year_income = 0
    #     year_outcome = 0
    #     year_pending_income = 0
    #     year_pending_outcome = 0
    #     year_total_profit = 0
    #     year_sure_profit = 0
    #     year_avg_profit = 0
    #     year_avg_income = 0
    #     year_avg_outcome = 0
    #     for i in range(12):
    #         month_date = start_date + relativedelta(months=i)
    #         next_month_date = month_date + \
    #             relativedelta(months=1) - relativedelta(days=1)

    #         # Filter bookings and payments for the current month
    #         bookings_for_month = bookings.filter(
    #             start_date__lte=next_month_date, end_date__gte=month_date)
    #         payments_for_month = payments.filter(
    #             payment_date__gte=month_date, payment_date__lte=next_month_date)

    #         month_income, month_outcome, month_pending_income, month_pending_outcome = aggregate_data(
    #             payments_for_month)
    #         total_days_in_month = next_month_date.day
    #         total_booked_days = calculate_total_booked_days(
    #             bookings_for_month, month_date, next_month_date)

    #         month_sure_profit = month_income - month_outcome
    #         month_total_profit = month_income + month_pending_income - \
    #             month_outcome - month_pending_outcome

    #         created_apartments_len = len(
    #             apartments.filter(created_at__lte=next_month_date))

    #         # if created_apartments_len > 0:
    #         month_occupancy = round(
    #             (total_booked_days / (total_days_in_month * apartments_len)) * 100)
    #         month_avg_outcome = round(month_outcome / apartments_len)
    #         month_avg_income = round(month_income / apartments_len)
    #         month_avg_profit = round(
    #             (month_income + month_pending_income - month_outcome - month_pending_outcome) / apartments_len)
    #         # else:
    #         # month_occupancy = 0
    #         # month_avg_outcome = 0
    #         # month_avg_income = 0
    #         # month_avg_profit = 0

    #         apartments_month_data.append({
    #             'date': month_date.strftime('%b'),
    #             'month_income': round(month_income),
    #             'month_outcome': round(month_outcome),
    #             'month_pending_income': round(month_pending_income),
    #             'month_pending_outcome': round(month_pending_outcome),
    #             'month_sure_profit': round(month_sure_profit),
    #             'month_expectied_proift': round(month_total_profit),
    #             'month_occupancy': month_occupancy,
    #             'month_avg_profit': month_avg_profit,
    #             'month_avg_income': month_avg_income,
    #             'month_avg_outcome': month_avg_outcome,
    #             'month_apartments_length': created_apartments_len,
    #         })

    #         year_income += month_income
    #         year_outcome += month_outcome
    #         year_pending_income += month_pending_income
    #         year_pending_outcome += month_pending_outcome
    #         year_total_profit += month_total_profit
    #         year_sure_profit += month_sure_profit
    #         year_occupancy += month_occupancy
    #         year_avg_outcome += month_avg_outcome
    #         year_avg_income += month_avg_income
    #         year_avg_profit += month_avg_profit

    #     apartments_data["apartments_month_data"] = apartments_month_data
    #     apartments_data["year_income"] = round(year_income)
    #     apartments_data["year_outcome"] = round(year_outcome)
    #     apartments_data["year_pending_income"] = round(year_pending_income)
    #     apartments_data["year_pending_outcome"] = round(year_pending_outcome)
    #     apartments_data["year_expectied_proift"] = round(year_total_profit)
    #     apartments_data["year_sure_profit"] = round(year_sure_profit)
    #     apartments_data["year_avg_profit"] = round(
    #         year_total_profit/12/apartments_len)
    #     apartments_data["year_avg_income"] = round(
    #         (year_income + year_pending_income)/12/apartments_len)
    #     apartments_data["year_avg_outcome"] = round(
    #         (year_outcome + year_pending_outcome)/12/apartments_len)
    #     apartments_data["year_occupancy"] = round(year_occupancy / 12)

    #     selected_apartments_data = []

    #     for apartment in selected_apartments:
    #         selected_apartment = {
    #             'apartment': apartment,
    #             'month_data': [],
    #             "year_income": 0,
    #             "year_outcome": 0,
    #             "year_pending_income": 0,
    #             "year_pending_outcome": 0,
    #             "year_total_profit": 0,
    #             "year_sure_profit": 0,
    #             "year_occupancy": 0,
    #             "year_avg_profit": 0,
    #             "year_avg_income": 0,
    #             "year_avg_outcome": 0,
    #         }

    #         for i in range(12):
    #             month_date = start_date + relativedelta(months=i)
    #             next_month_date = month_date + \
    #                 relativedelta(months=1) - relativedelta(days=1)

    #             # Filter bookings and payments for the current month and selected apartment
    #             bookings_for_month = bookings.filter(start_date__lte=next_month_date,
    #                                                  end_date__gte=month_date, apartment=apartment)
    #             payments_for_month = payments.filter(
    #                 Q(apartment=apartment) | Q(booking__apartment=apartment),
    #                 payment_date__gte=month_date,
    #                 payment_date__lte=next_month_date
    #             )

    #             month_income, month_outcome, month_pending_income, month_pending_outcome = aggregate_data(
    #                 payments_for_month)
    #             total_days_in_month = next_month_date.day

    #             total_booked_days = calculate_unique_booked_days(
    #                 bookings_for_month, month_date, next_month_date)

    #             month_occupancy = round(
    #                 (total_booked_days / (total_days_in_month)) * 100)

    #             month_sure_profit = month_income - month_outcome
    #             month_total_profit = month_income + month_pending_income - \
    #                 month_outcome - month_pending_outcome

    #             # You can calculate more metrics here

    #             selected_apartment['month_data'].append({
    #                 'month_date': month_date.strftime('%b'),
    #                 'month_income': round(month_income),
    #                 'month_outcome': round(month_outcome),
    #                 'month_pending_income': round(month_pending_income),
    #                 'month_pending_outcome': round(month_pending_outcome),
    #                 'month_total_profit': round(month_total_profit),
    #                 'month_sure_profit': round(month_sure_profit),
    #                 'month_occupancy': round(month_occupancy),
    #                 'total_days_in_month': total_days_in_month,
    #                 'total_booked_days': total_booked_days,
    #             })
    #             selected_apartment["year_income"] += month_income
    #             selected_apartment["year_outcome"] += month_outcome
    #             selected_apartment["year_pending_income"] += month_pending_income
    #             selected_apartment["year_pending_outcome"] += month_pending_outcome
    #             selected_apartment["year_total_profit"] += month_total_profit
    #             selected_apartment["year_sure_profit"] += month_sure_profit
    #             selected_apartment["year_occupancy"] += month_occupancy

    #         selected_apartment["year_avg_profit"] = round(
    #             selected_apartment["year_total_profit"]/12)
    #         selected_apartment["year_avg_income"] = round(
    #             (selected_apartment["year_income"] + selected_apartment["year_pending_income"])/12)
    #         selected_apartment["year_avg_outcome"] = round(
    #             (selected_apartment["year_outcome"] + selected_apartment["year_pending_outcome"])/12)
    #         selected_apartments_data.append(selected_apartment)
    #         selected_apartment["year_occupancy"] = round(
    #             selected_apartment["year_occupancy"]/12)

    #     apartments_data["selected_apartments_data"] = selected_apartments_data
    #     apartments_data_str_keys = stringify_keys(apartments_data)
    #     apartments_data_json = json.dumps(apartments_data_str_keys, default=str)

    #     aprat_len = apartments_data["apartments_month_data"][-1]["month_apartments_length"]

    #     context = {
    #         'apartments_data': apartments_data,
    #         'apartments': apartments,
    #         'apartments_data_json': apartments_data_json,
    #         'apartment_ids': apartment_ids,
    #         'current_year': today.year,
    #         'year_range': year_range,
    #         'aprat_len': aprat_len,
    #         'year': year
    #     }

    #     return render(request, 'apartments_analytics.html', context)


    # def getMockData():
    #     text_fields = [
    #         {"tabLabel": "tenant_email", "value": "johnnypitt.ind@gmail.com"},
    #         {"tabLabel": "tenant_full_name", "value": "Johnny Pitt"},
    #         {"tabLabel": "tenant_phone", "value": "+1 234 567 8901"},
    #         {"tabLabel": "owner_phone", "value": "+1 098 765 4321"},
    #         {"tabLabel": "owner_full_name", "value": "Anna Smith"},
    #         {"tabLabel": "apartment_state", "value": "California"},
    #         {"tabLabel": "apartment_city", "value": "San Francisco"},
    #         {"tabLabel": "apartment_street", "value": "Market Street"},
    #         {"tabLabel": "apartment_number", "value": "123"},
    #         {"tabLabel": "apartment_room", "value": "45A"},
    #         {"tabLabel": "booking_start_date", "value": "2023-09-20"},
    #         {"tabLabel": "booking_end_date", "value": "2023-09-30"},
    #         {"tabLabel": "payments", "value": "1500.00"}
    #     ]
    #     # Convert to Text objects
    #     text_tabs = [Text(tab_label=item["tabLabel"], value=item["value"]) for item in text_fields]

    #     # Create a Tabs object containing only text_tabs
    #     tabs = Tabs(text_tabs=text_tabs)

    #     return tabs


    # def get_user(access_token):
    #     """Make request to the API to get the user information"""
    #     # Determine user, account_id, base_url by calling OAuth::getUserInfo
    #     # See https://developers.docusign.com/esign-rest-api/guides/authentication/user-info-endpoints

    #     url = "https://account-d.docusign.com/oauth/userinfo"
    #     auth = {"Authorization": "Bearer " + access_token}
    #     response = requests.get(url, headers=auth).json()

    #     return response


    # def get_docusign_token():
    #     api_client = ApiClient()
    #     api_client.set_base_path(os.environ["DOCUSIGN_API_URL"])
    #     private_key_content = os.environ["DOCUSIGN_PRIVATE_KEY"]
    #     if '\\n' in private_key_content:
    #         private_key_content = private_key_content.replace('\\n', '\n')

    #     token = api_client.request_jwt_user_token(
    #         client_id=os.environ["DOCUSIGN_INTEGRATION_KEY"],
    #         user_id=os.environ["DOCUSIGN_USER_ID"],
    #         oauth_host_name=os.environ["DOCUSIGN_AUTH_SERVER"],
    #         private_key_bytes=private_key_content.encode('utf-8'),
    #         expires_in=3600,
    #         scopes=["signature"]
    #     )

    #     if token is None:
    #         logger.error("Error while requesting token")
    #     else:
    #         api_client.set_default_header("Authorization", "Bearer " + token.access_token)
    #         user = get_user(token.access_token)

    #     return token.access_token


    # def create_api_client(access_token):
    #     """Create api client and construct API headers"""
    #     api_client = ApiClient()
    #     api_client.host = "https://demo.docusign.net/restapi"
    #     api_client.set_default_header(header_name="Authorization", header_value=f"Bearer {access_token}")

    #     return api_client


    # def docusign(request):
    #     token = get_docusign_token()
    #     api_client = create_api_client(token)
    #     envelope_api = EnvelopesApi(api_client)

    #     tabs = getMockData()

    #     tenant = TemplateRole(
    #         email="andy.vaulin@gmail.com",
    #         name="Andy Vaulin",
    #         client_user_id="1244525",
    #         role_name="Tenant",
    #         tabs=tabs
    #     )

    #     # Create and send the envelope
    #     envelope_definition = EnvelopeDefinition(
    #         email_subject="Please sign this contract",
    #         template_id=os.environ.get("DOCUSIGN_TEMPLATE_ID"),
    #         template_roles=[tenant],
    #         status="sent"
    #     )
    #     envelope_summary = envelope_api.create_envelope(
    #         os.environ["DOCUSIGN_API_ACCOUNT_ID"],
    #         envelope_definition=envelope_definition)

    # event_notification = EventNotification(
    #     url=os.environ.get('DOCUSIGN_CALLBACK_URL'),
    #     logging_enabled='true',
    #     require_acknowledgment='true',
    #     envelope_events=[EnvelopeEvent(envelope_event_status_code='completed')],
    #     recipient_events=[RecipientEvent(recipient_event_status_code='completed')]
    # )

    # envelope_definition = EnvelopeDefinition(
    #     email_subject='Please sign this document',
    #     template_id=os.environ.get('DOCUSIGN_TEMPLATE_ID'),
    #     template_roles=[tenant_template_role],
    #     status='sent',
    #     event_notification=event_notification
    # )

    # recipient_view_request = RecipientViewRequest(
    #     authentication_method="none",
    #     client_user_id="1244525",
    #     recipient_id="1",
    #     return_url=f"http://localhost:8000/callback?envelopeId={envelope_summary.envelope_id}",
    #     user_name=tenant.name,
    #     email=tenant.email
    # )

    # results = envelope_api.create_recipient_view(
    #     account_id=os.environ["DOCUSIGN_API_ACCOUNT_ID"],
    #     envelope_id=envelope_summary.envelope_id,
    #     recipient_view_request=recipient_view_request
    # )

    # return JsonResponse({'status': 'success', "envelope_id": envelope_summary.envelope_id,
    #                      "envlope_uri": envelope_summary.uri})
    #     return JsonResponse({'status': 'success', "envelope_id": envelope_summary.envelope_id,
    #                         "envlope_uri": envelope_summary.uri, "redirect_url": results.url})


    # def callback(request):
    #     # Access query parameters from the GET request
    #     envelope_id = request.GET.get('envelopeId', None)
    #     event = request.GET.get('event', None)

    #     if event == 'signing_complete' and envelope_id:
    #         # Initialize your DocuSign API client
    #         token = get_docusign_token()
    #         api_client = create_api_client(token)
    #         envelopes_api = EnvelopesApi(api_client)

    #         # Fetch form data
    #         account_id = os.environ["DOCUSIGN_API_ACCOUNT_ID"]
    #         form_data_response = envelopes_api.get_form_data(account_id=account_id, envelope_id=envelope_id)

    #         # Process and parse form data
    #         recipient_form_data = form_data_response.recipient_form_data[0] if form_data_response.recipient_form_data else None
    #         form_data_items = recipient_form_data.form_data if recipient_form_data and recipient_form_data.form_data else []

    #         # Create a dictionary from form data items
    #         form_data_dict = {item.name: item.value for item in form_data_items}

    #         return JsonResponse({'status': 'success', 'formData': form_data_dict})

    #     return JsonResponse({'status': 'failed'})

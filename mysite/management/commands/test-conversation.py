import os
import time
from django.core.management.base import BaseCommand
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

class Command(BaseCommand):
    help = 'Test Twilio scenario'

    def handle(self, *args, **kwargs):
        # Set environment variables
        account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        auth_token = os.environ["TWILIO_AUTH_TOKEN"]
        # twilio_phone = os.environ["TWILIO_PHONE"]
        manager_phone = os.environ["MANAGER_PHONE"]
        second_manager_phone = os.environ["MANAGER_PHONE2"]
        twilio_phone_secondary = "+13153524379"
        farid_secondary = "+17282001917"
        farid_wife = "+15618438867"
        farid = "+15614603904"
        client = Client(account_sid, auth_token)

        self.delete_conversation("CHcf22935e48934c5891d360b4f54b2f1a")

        return
        time.sleep(10)
        self.stdout.write(self.style.SUCCESS(f'Deleted conversations'))
        time.sleep(10)
        # Step 1: Create the Conversation
        conversation = client.conversations.v1.conversations.create(
            friendly_name="Home-buying journey 2"
        )
        conversation_sid = conversation.sid
        self.stdout.write(self.style.SUCCESS(f'Created conversation: {conversation_sid}'))
        time.sleep(10)

        # Step 2: Add the Real Estate Agent
        participant = client.conversations.v1.conversations(
            conversation_sid
        ).participants.create(
            identity="realEstateAgent",
            messaging_binding_projected_address=twilio_phone_secondary,
        )
        self.stdout.write(self.style.SUCCESS(f'Added real estate agent: {participant.sid}'))
        time.sleep(10)

        # Step 3: Add the First Homebuyer
        participant = client.conversations.v1.conversations(
            conversation_sid
        ).participants.create(messaging_binding_address=farid)
        self.stdout.write(self.style.SUCCESS(f'Added first homebuyer: {participant.sid}'))
        time.sleep(30)

        # Step 4: Send a 1:1 Message
        message = client.conversations.v1.conversations(
            conversation_sid
        ).messages.create(
            body="Hi there. What did you think of the listing I sent?",
            author="realEstateAgent",
        )
        self.stdout.write(self.style.SUCCESS(f'Sent message: {message.sid}'))
        time.sleep(10)

        # Step 5: Add the Second Homebuyer
        participant = client.conversations.v1.conversations(
            conversation_sid
        ).participants.create(messaging_binding_address=second_manager_phone)
        self.stdout.write(self.style.SUCCESS(f'Added second homebuyer: {participant.sid}'))
        time.sleep(30)

        # Step 6: Send Another Message
        message = client.conversations.v1.conversations(
            conversation_sid
        ).messages.create(
            body="Glad you could join us, homebuyer 2. I really love these granite countertops and think you will as well.",
            author="realEstateAgent",
        )
        self.stdout.write(self.style.SUCCESS(f'Sent message: {message.sid}'))
        time.sleep(30)


        #Step 5: Add the Third Homebuyer
        participant = client.conversations.v1.conversations(
            conversation_sid
        ).participants.create(messaging_binding_address=farid_wife)
        self.stdout.write(self.style.SUCCESS(f'Added third homebuyer: {participant.sid}'))
        time.sleep(30)

        message = client.conversations.v1.conversations(
            conversation_sid
        ).messages.create(
            body="Hi. I'm homebuyer 3. I'm looking for a house.",
            author="realEstateAgent",
        )
        self.stdout.write(self.style.SUCCESS(f'Sent message: {message.sid}'))
        time.sleep(30)

    def delete_conversation(self, conversation_sid):
        account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        auth_token = os.environ["TWILIO_AUTH_TOKEN"]
        client = Client(account_sid, auth_token)

        try:
            client.conversations.v1.conversations(conversation_sid).delete()
            self.stdout.write(self.style.SUCCESS(f'Deleted conversation: {conversation_sid}'))
        except TwilioRestException as e:
            self.stdout.write(self.style.SUCCESS(f'Some Error: {e}'))


# Created conversation: CHa86aade3c19f4e84b18b6bb5675b04d7
# Added real estate agent: MB75db52b0e31f41babe5be4f17f5f6f97
# Added first homebuyer: MB0eb9325a64b3425f9c360ed2d3811d76
# Sent message: IMb2553843376f4756a87bea18ce74be7d
# Traceback (most recent call last):
#   File "/Users/hallojohnnypitt/Projects/property-managment/site/manage.py", line 22, in <module>
#     main()
#   File "/Users/hallojohnnypitt/Projects/property-managment/site/manage.py", line 18, in main
#     execute_from_command_line(sys.argv)
#   File "/Users/hallojohnnypitt/Projects/property-managment/site/myenv/lib/python3.9/site-packages/django/core/management/__init__.py", line 442, in execute_from_command_line
#     utility.execute()
#   File "/Users/hallojohnnypitt/Projects/property-managment/site/myenv/lib/python3.9/site-packages/django/core/management/__init__.py", line 436, in execute
#     self.fetch_command(subcommand).run_from_argv(self.argv)
#   File "/Users/hallojohnnypitt/Projects/property-managment/site/myenv/lib/python3.9/site-packages/django/core/management/base.py", line 412, in run_from_argv
#     self.execute(*args, **cmd_options)
#   File "/Users/hallojohnnypitt/Projects/property-managment/site/myenv/lib/python3.9/site-packages/django/core/management/base.py", line 458, in execute
#     output = self.handle(*args, **options)
#   File "/Users/hallojohnnypitt/Projects/property-managment/site/mysite/management/commands/test-conversation.py", line 61, in handle
#     participant = client.conversations.v1.conversations(
#   File "/Users/hallojohnnypitt/Projects/property-managment/site/myenv/lib/python3.9/site-packages/twilio/rest/conversations/v1/conversation/participant.py", line 571, in create
#     payload = self._version.create(
#   File "/Users/hallojohnnypitt/Projects/property-managment/site/myenv/lib/python3.9/site-packages/twilio/base/version.py", line 465, in create
#     return self._parse_create(method, uri, response)
#   File "/Users/hallojohnnypitt/Projects/property-managment/site/myenv/lib/python3.9/site-packages/twilio/base/version.py", line 436, in _parse_create
#     raise self.exception(method, uri, response, "Unable to create record")
# twilio.base.exceptions.TwilioRestException: 
# HTTP Error Your request was:

# POST /Conversations/CHa86aade3c19f4e84b18b6bb5675b04d7/Participants

# Twilio returned the following information:

# Unable to create record: Group MMS with given participant list already exists as Conversation CH206481d1aa2f4320bc3883897dac11e0

# More information may be available here:

# https://www.twilio.com/docs/errors/50438
import requests
from datetime import timedelta, date
from mysite.models import Notification  
import os

TELEGRAM_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'
CHAT_ID = 'YOUR_TELEGRAM_CHAT_ID'

def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)

def my_cron_job():
    
    next_day = date.today() + timedelta(days=1)
    notifications = Notification.objects.filter(date=next_day)
    
    send_telegram_message(os.environ["TELEGRAM_CHAT_ID"], os.environ["TELEGRAM_TOKEN"], "Test Message")  
    
    # for notification in notifications:
      
    #     message = f"Notification: {notification.message}"
        
        
    #     send_telegram_message(os.environ["TELEGRAM_CHAT_ID"], os.environ["TELEGRAM_TOKEN"], message)

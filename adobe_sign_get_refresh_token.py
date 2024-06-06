import requests
import os
from flask import Flask, request, redirect, url_for
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Replace these values with your Adobe Sign application details
CLIENT_ID = os.environ.get("ADOBE_SIGN_CLIENT_ID")
CLIENT_SECRET = os.environ.get("ADOBE_SIGN_CLIENT_SECRET")
REDIRECT_URI = 'https://d7dwj16v-8005.asse.devtunnels.ms/callback'  # Change this to your production URL if needed

@app.route('/')
def home():
    
    return f'Hellow Word'

@app.route('/get-url')
def get_url():
    auth_url = (
        f"https://secure.na4.adobesign.com/public/oauth/v2?"
        f"redirect_uri={REDIRECT_URI}&"
        f"response_type=code&"
        f"client_id={CLIENT_ID}&"
        f"scope=agreement_read agreement_write agreement_send"
    )
    print(auth_url)
    return f'<a href="{auth_url}" target="_blunk">Authorize Application</a>'

@app.route('/callback')
def callback():
    authorization_code = request.args.get('code')
    token_url = "https://api.na4.adobesign.com/oauth/v2/token"
    payload = {
        "grant_type": "authorization_code",
        "code": authorization_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI
    }

    response = requests.post(token_url, data=payload)
    response_data = response.json()

    if response.status_code == 200:
        refresh_token = response_data.get("refresh_token")
        return f"Refresh Token: {refresh_token}"
    else:
        return f"Failed to obtain refresh token: {response_data}"

if __name__ == '__main__':
    app.run(port=8005)
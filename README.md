## Django Property Administration

```python manage.py runserver       ```

## Deploying on Railway
Create account: https://railway.app/
Hobby Plan
The Hobby plan costs $5 per month and includes up to 5 of usage. Additional resource usage is billed separately.
Memory per container: 8 GB
CPU per container: 8 vCPU
Shared disk: 100 GB

https://www.npmjs.com/package/@railway/cli to install raillway locally
```railway login``` to login.
```railway link b16af7a5-d954-4afd-ad74-c66141cf9f39``` to link project (find link for the project project settings)
```railway up``` to deploy.


# Local Development

## Python Environment Setup

### 1. Navigate to Your Project Directory:
```cd path/to/your/project```

### 2. Create a Virtual Environment:
 ```pip install virtualenv```

### 3. reate a virtual environment in your project directory.
```virtualenv venv```

### 4. Activate the Virtual Environment:
 ```source venv/bin/activate```

## Python Install
```pip install -r requirements.txt```

## Node Install
```npm install```

## Watch Tailwind
```npx tailwindcss -i ./static/input.css -o ./static/output.css --watch```

## Compile Tailwind
```npx tailwindcss -i ./static/input.css -o ./static/output.css```


## DEBUG
```railway logs```

## Apply migration
```python manage.py migrate```

## Make Migrations after changes
```python manage.py makemigrations```


## Seeds
```python manage.py seed_db```


## Dumb And Restore Data
```python manage.py dumpdata > backup.json```
```python manage.py flush; python manage.py loaddata backup.json```

## Update Cron job
```python manage.py crontab add```
## Show Cron jobs
```python manage.py crontab show```
## Remove Cron jobs
```python manage.py crontab remove```

### Consent URL
https://account-d.docusign.com/oauth/auth?response_type=code&scope=signature%20impersonation&client_id=e235ff67-bdf0-475e-8bf1-6c26da649954&redirect_uri=http://localhost:8000/


systemctl restart site
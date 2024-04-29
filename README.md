## Django Property Administration

```python manage.py runserver       ```

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


## Apply migration
```python manage.py migrate```

## Make Migrations after changes
```python manage.py makemigrations```



## Dumb And Restore Data
```python manage.py dumpdata > backup.json```
```python manage.py flush; python manage.py loaddata backup.json```




systemctl restart site localhost
pg_dump -U postgres_user -h 68.183.124.79 -d railway > backup.sql
psql -U postgres_user -h 68.183.124.79 -d railway < data_backup.sql
psql -U postgres_user -h localhost -d railway < backup.sql

pg_dump -U postgres_user -h localhost -d railway > backup_localhost.sql

psql -U hallojohnnypitt -d postgres
 


psql -U postgres_user -h 68.183.124.79 -d railway < backup_server.sql

pg_dump -h localhost -U postgres_user -d railway --table=mysite_apartment --table=mysite_booking --table=mysite_cleaning --table=mysite_notification --table=mysite_payment --table=mysite_paymentmethod --table=mysite_paymentype --table=mysite_user > data_backup.sql



psql -U postgres_user -h 68.183.124.79 -d postgres -c "DROP DATABASE railway;"

local psql start
pg_ctl -D /usr/local/var/postgresql@14/ start

lsof -ti tcp:8000 | xargs kill -9

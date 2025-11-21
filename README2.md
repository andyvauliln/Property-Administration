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
 
 psql -U postgres_user -d railway


psql -U postgres_user -h 68.183.124.79 -d railway < backup_server.sql

pg_dump -h localhost -U postgres_user -d railway --table=mysite_apartment --table=mysite_booking --table=mysite_cleaning --table=mysite_notification --table=mysite_payment --table=mysite_paymentmethod --table=mysite_paymentype --table=mysite_user > data_backup.sql



psql -U postgres_user -h 68.183.124.79 -d postgres -c "DROP DATABASE railway;"

local psql start
pg_ctl -D /usr/local/var/postgresql@14/ start

lsof -ti tcp:8000 | xargs kill -9
sudo systemctl restart site
Bdga58BVUPCXCHif

/etc/postgresql/14/main/pg_hba.conf

 pg_dump -U postgres_user -W -F c -b -v -f ./backup_2024_07_08.dump railway
 createdb -U postgres_user -h 68.183.124.79 railway_v2
eEdjNBNeHtIvzO5RnzDJ
   ALTER USER postgres WITH PASSWORD 'eEdjNBNeHtIvzO5RnzDJ';

export PGPASSWORD='eEdjNBNeHtIvzO5RnzDJ'
psql -U postgres_user -h 68.183.124.79 -d railway_v2 -f backup.sql
current mysite  


host    railway        postgres_user    103.225.151.166/32 md5
host    all             all             0.0.0.0/0 
server {
    listen 80;
    server_name 68.183.124.79;

    location = /favicon.ico { access_log off; log_not_found off; }
    location /static/ {
        root /home/superuser/site;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/home/superuser/site/mysite.sock;
    }
}

new site configurations                                                              
server {
    listen 80;
    server_name v2.68.183.124.79;

    location = /favicon.ico { access_log off; log_not_found off; }
    location /static/ {
        root /home/superuser/new_payment_methods;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/home/superuser/new_payment_methods/new_payment_methods.sock;
    }
}

Current site configuration
[Unit]
Description=gunicorn daemon for site
After=network.target

[Service]
User=superuser
Group=superuser
WorkingDirectory=/home/superuser/site
ExecStartPre=/bin/bash -c 'source /home/superuser/site/venv/bin/activate'
ExecStart=/usr/bin/gunicorn --workers 3 --bind unix:/home/superuser/site/mysite.sock mysite.wsgi:application
ExecReload=/bin/kill -s HUP $MAINPID
ExecStop=/bin/kill -s TERM $MAINPID
ExecStartPre=/bin/sleep 5
ExecStartPre=/bin/bash -c 'source /home/superuser/site/venv/bin/activate'
ExecStartPre=pm2 start /home/superuser/site/pm2.config.js
[Install]
WantedBy=multi-user.target


new site configuration
[Unit]
Description=gunicorn daemon for site
After=network.target

[Service]
User=superuser
Group=superuser
WorkingDirectory=/home/superuser/new_payment_methods
ExecStartPre=/bin/bash -c 'source /home/superuser/new_payment_methods/venv/bin/activate'
ExecStart=/usr/bin/gunicorn --workers 3 --bind unix:/home/superuser/new_payment_methods/new_payment_methods.sock mysite.wsgi:application
ExecReload=/bin/kill -s HUP $MAINPID
ExecStop=/bin/kill -s TERM $MAINPID
ExecStartPre=/bin/sleep 5
ExecStartPre=/bin/bash -c 'source /home/superuser/new_payment_methods/venv/bin/activate'
ExecStartPre=pm2 start /home/superuser/new_payment_methods/pm2.config.js
[Install]
WantedBy=multi-user.target


SELECT *
FROM mysite_notification
WHERE date = '2025-03-01'
  AND id IN (4116,4242,4243,4244,4286,4509,4527,4619,4729,4819,4825,4916,5065,5090,5169,5189,5199,5775 
  )
ORDER BY id LIMIT 100

PGPASSWORD=eEdjNBNeHtIvzO5RnzDJ psql -h localhost -U postgres_user -d railway -c "SELECT *
FROM mysite_notification
WHERE date = '2025-03-01'
  AND id IN (5615,4116,4242,4243,4244,4286,4509,4527,4619,4729,4819,4825,4916,5065,5090,5169,5189,5199,5775 
  )
ORDER BY id LIMIT 100" > march_3_2025_notifications.txt

SELECT n.id as notification_id, 
       n.date as notification_date,
       p.amount,
       p.status,
       p.unit_number,
       p.payment_type
FROM mysite_notification n
LEFT JOIN mysite_payment p ON n.payment_id = p.id
WHERE n.date = '2025-03-01'
  AND n.id IN (
    4116,
    4242,
    4243,
    4244,
    4286,
    4509,
    4527,
    4619,
    4729,
    4819,
    4825,
    4916,
    5065,
    5090,
    5169,
    5189,
    5199,
    5775,
    5615 
  )
ORDER BY n.id;
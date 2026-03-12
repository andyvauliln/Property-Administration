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






1) Test matching with ai
2) Test matching manual
3) Test matching both
4) Test update
5) Test create new payment
6) Test on a file
7) Add Payment Delete and update

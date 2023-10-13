# Database backed Celery Beat Scheduler

## Description
This is an sqlmodel based implementation of the celery beat scheduler.
It persists periodic celery tasks in a SQLAlchemy-compatible database.
I built this because celery-sqlalchemy-scheduler is not maintained and does not support ClockedSchedules.

## Usage
You can install this package using pip.
After installation, specify the database connection string in the Celery config, using the name `beat_dburi`.

You can run the beat instance using:
```bash
celery -A {{app_name}} beat --scheduler sqlmodel_celery_beat.schedulers:DatabaseScheduler -l INFO
```





## Acknowledgements
During the development of this project I used the following projects as reference:
- celery-sqlalchemy-scheduler
- django-celery-beat
- celerybeatredis
- celery
- sqlmodel

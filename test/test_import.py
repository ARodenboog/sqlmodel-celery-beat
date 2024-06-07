## Simple import test to ensure that the module can be imported


def test_import():
    try:
        from sqlmodel_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule, SolarSchedule, ClockedSchedule
    except Exception as e:
        raise AssertionError(f"Import failed: {e}")


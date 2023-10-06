from enum import Enum
from typing import Optional
from celery import current_app
from cron_descriptor import (
    FormatException,
    MissingFieldException,
    WrongArgumentException,
    get_description,
)
from pydantic import ValidationError, root_validator, validator
import sqlalchemy as sa
from sqlmodel import Relationship, SQLModel, Field, Session, select
from celery.schedules import schedules
from datetime import timedelta, datetime
from sqlmodel_celery_beat.clockedschedule import clocked
from sqlmodel_celery_beat.tzcrontab import TzAwareCrontab

from util import nowfun, make_aware


def cronexp(field: str) -> str:
    """Representation of cron expression."""
    return field and str(field).replace(" ", "") or "*"


class ModelMixin(SQLModel):
    """Base model mixin"""

    id = Field(int, primary_key=True)
    created_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
        default=nowfun(),
    )
    updated_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
        default_factory=nowfun,
    )

    @classmethod
    def create(cls, **kwargs):
        return cls(**kwargs)

    def update(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        return self

    def save(self, session: Session, *args, **kwargs):
        session.add(self)
        session.commit()


class IntervalPeriod(Enum):
    """Enumeration of interval periods."""

    DAYS = "days"
    HOURS = "hours"
    MINUTES = "minutes"
    SECONDS = "seconds"


class IntervalSchedule(ModelMixin, table=True):
    """Schedule executing every n seconds, minutes, hours or days."""

    every: int = 0
    period: IntervalPeriod = Field(
        sa_column=sa.Column(
            sa.Enum(IntervalPeriod, create_constraint=True),
            default=IntervalPeriod.SECONDS,
        )
    )

    @property
    def schedule(self):
        return schedules.schedule(timedelta(**{self.period: self.every}), nowfun=nowfun)

    def __str__(self):
        return f"every {self.every} {self.period}"


class SolarEvent(Enum):
    """Enumeration of solar events."""

    SUNRISE = "sunrise"
    SUNSET = "sunset"
    DAWN_ASTRONOMICAL = "dawn_astronomical"
    DAWN_CIVIL = "dawn_civil"
    DAWN_NAUTICAL = "dawn_nautical"
    DUSK_ASTRONOMICAL = "dusk_astronomical"
    DUSK_CIVIL = "dusk_civil"
    DUSK_NAUTICAL = "dusk_nautical"
    SOLAR_NOON = "solar_noon"


class SolarSchedule(ModelMixin, table=True):
    """Schedule following astronomical patterns.

    Example: to run every sunrise in New York City:

    >>> event='sunrise', latitude=40.7128, longitude=74.0060
    """

    event: SolarEvent = Field(
        sa_column=sa.Column(sa.Enum(SolarEvent, create_constraint=True))
    )
    latitude: float
    longitude: float

    @validator("latitude")
    def validate_latitude(cls, v):
        if v < -90 or v > 90:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @validator("longitude")
    def validate_longitude(cls, v):
        if v < -180 or v > 180:
            raise ValueError("longitude must be between -180 and 180")
        return v

    @property
    def schedule(self):
        return schedules.solar(
            self.event,
            self.latitude,
            self.longitude,
            nowfun=lambda: make_aware(nowfun()),
        )

    def __str__(self):
        return "{} ({}, {})".format(
            self.get_event_display(), self.latitude, self.longitude
        )


class ClockedSchedule(ModelMixin, table=True):
    """Clocked schedule, run once at a specific time."""

    clocked_time: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True)), nullable=False
    )

    def __str__(self):
        return f"{make_aware(self.clocked_time)}"

    @property
    def schedule(self):
        c = clocked(clocked_time=self.clocked_time)
        return c


class CrontabSchedule(ModelMixin, table=True):
    """Timezone Aware Crontab-like schedule.

    Example:  Run every hour at 0 minutes for days of month 10-15:

    >>> minute="0", hour="*", day_of_week="*",
    ... day_of_month="10-15", month_of_year="*"
    """

    #
    # The worst case scenario for day of month is a list of all 31 day numbers
    # '[1, 2, ..., 31]' which has a length of 115. Likewise, minute can be
    # 0..59 and hour can be 0..23. Ensure we can accomodate these by allowing
    # 4 chars for each value (what we save on 0-9 accomodates the []).
    # We leave the other fields at their historical length.
    #
    minute: str = Field(max_length=60 * 4, default="*")
    hour: str = Field(max_length=24 * 4, default="*")
    day_of_week: str = Field(max_length=64, default="*")
    day_of_month: str = Field(max_length=31 * 4, default="*")
    month_of_year: str = Field(max_length=64, default="*")

    @property
    def human_readable(self):
        cron_expression = "{} {} {} {} {}".format(
            cronexp(self.minute),
            cronexp(self.hour),
            cronexp(self.day_of_month),
            cronexp(self.month_of_year),
            cronexp(self.day_of_week),
        )
        try:
            human_readable = get_description(cron_expression)
        except (MissingFieldException, FormatException, WrongArgumentException):
            return f"{cron_expression} {str(self.timezone)}"
        return f"{human_readable} {str(self.timezone)}"

    def __str__(self):
        return "{} {} {} {} {} (m/h/dM/MY/d) {}".format(
            cronexp(self.minute),
            cronexp(self.hour),
            cronexp(self.day_of_month),
            cronexp(self.month_of_year),
            cronexp(self.day_of_week),
            str(self.timezone),
        )

    @property
    def schedule(self):
        crontab = schedules.crontab(
            minute=self.minute,
            hour=self.hour,
            day_of_week=self.day_of_week,
            day_of_month=self.day_of_month,
            month_of_year=self.month_of_year,
        )
        if getattr(current_app.conf, "CELERY_BEAT_TZ_AWARE", True):
            crontab = TzAwareCrontab(
                minute=self.minute,
                hour=self.hour,
                day_of_week=self.day_of_week,
                day_of_month=self.day_of_month,
                month_of_year=self.month_of_year,
                tz=self.timezone,
            )
        return crontab

    @classmethod
    def from_schedule(cls, session: Session, schedule: schedules.crontab):
        spec = {
            "minute": schedule._orig_minute,
            "hour": schedule._orig_hour,
            "day_of_week": schedule._orig_day_of_week,
            "day_of_month": schedule._orig_day_of_month,
            "month_of_year": schedule._orig_month_of_year,
            "timezone": schedule.tz,
        }
        try:
            return session.get(cls, **spec)
        except sa.orm.exc.NoResultFound:
            return cls(**spec)
        except sa.orm.exc.MultipleResultsFound:
            return session.exec(select(cls), **spec).first()


class PeriodicTasksChanged(ModelMixin, table=True):
    """Helper table for tracking updates to periodic tasks.

    This stores a single row with ``ident=1``. ``last_update`` is updated via
    signals whenever anything changes in the :class:`~.PeriodicTask` model.
    Basically this acts like a DB data audit trigger.
    Doing this so we also track deletions, and not just insert/update.
    """

    ident: int = Field(default=1, primary_key=True)
    last_update: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
        default=nowfun(),
    )

    @classmethod
    def changed(cls, session: Session, instance, **kwargs):
        # TODO: Check how this works with sqlalchemy
        if not instance.no_changes:
            cls.update_changed()

    @classmethod
    def update_changed(cls, session: Session, **kwargs):
        cls.objects.update_or_create(ident=1, defaults={"last_update": nowfun()})

    @classmethod
    def last_change(cls, session: Session) -> Optional[datetime]:
        try:
            return session.get(cls, ident=1).last_update
        except sa.orm.exc.NoResultFound:
            pass

class PeriodicTask(ModelMixin, table=True):
    """Model representing a periodic task."""

    name: str = Field(max_length=200, unique=True)
    task: str = Field(max_length=200)

    interval_id: Optional[int] = Field(default=None, foreign_key="intervalschedule.id")
    interval: Optional[IntervalSchedule] = Relationship(back_populates="periodic_task")

    crontab_id: Optional[int] = Field(default=None, foreign_key="crontabschedule.id")
    cron: Optional[CrontabSchedule] = Relationship(back_populates="periodic_task")

    solar_id: Optional[int] = Field(default=None, foreign_key="solarschedule.id")
    solar: Optional[SolarSchedule] = Relationship(back_populates="periodic_task")

    clocked_id: Optional[int] = Field(default=None, foreign_key="clockedschedule.id")
    clocked: Optional[ClockedSchedule] = Relationship(back_populates="periodic_task")

    args: list = Field(sa_column=sa.Column(sa.JSON, nullable=False), default_factory=list)
    kwargs: dict = Field(sa_column=sa.Column(sa.JSON, nullable=False), default_factory=dict)

    queue: Optional[str] = Field(max_length=200, nullable=True)

    # you can use low-level AMQP routing options here,
    # but you almost certaily want to leave these as None
    # http://docs.celeryproject.org/en/latest/userguide/routing.html#exchanges-queues-and-routing-keys
    exchange: Optional[str] = Field(max_length=200, nullable=True)
    routing_key: Optional[str] = Field(max_length=200, nullable=True)
    headers: Optional[dict] = Field(sa_column=sa.Column(sa.JSON, nullable=False), default_factory=dict)
    priority: Optional[int] = Field(default=None, nullable=True)
    expires: Optional[datetime] = Field(sa_column=sa.Column(sa.DateTime(timezone=True)), nullable=True)
    expire_seconds: Optional[int] = Field(default=None, nullable=True)
    one_off: bool = Field(default=False)
    start_time: Optional[datetime] = Field(sa_column=sa.Column(sa.DateTime(timezone=True)), nullable=True)
    enabled: bool = Field(default=True)
    last_run_at: Optional[datetime] = Field(sa_column=sa.Column(sa.DateTime(timezone=True)), nullable=True)
    total_run_count: int = Field(default=0, nullable=False)
    date_changed: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
        default=nowfun(),
    )
    description: str = Field(max_length=200, nullable=True)

    no_changes: bool = False

    @root_validator
    def validate_unique(cls, values: dict, **kwargs):

        schedule_types = ['interval', 'crontab', 'solar', 'clocked']
        selected_schedule_types = [s for s in schedule_types
                                   if values.get(s) is not None]

        if len(selected_schedule_types) == 0:
            raise ValidationError(
                'One of clocked, interval, crontab, or solar '
                'must be set.'
            )

        err_msg = 'Only one of clocked, interval, crontab, '\
            'or solar must be set'
        if len(selected_schedule_types) > 1:
            error_info = {}
            for selected_schedule_type in selected_schedule_types:
                error_info[selected_schedule_type] = [err_msg]
            raise ValidationError(error_info)

        # clocked must be one off task
        if values["clocked"] and not values["one_off"]:
            err_msg = 'clocked must be one off, one_off must set True'
            raise ValidationError(err_msg)
        if (values["expires_seconds"] is not None) and (values["expires"] is not None):
            raise ValidationError(
                'Only one can be set, in expires and expire_seconds'
            )


    def save(self, session: Session, *args, **kwargs):
        if not self.enabled:
            self.last_run_at = None
        session.add(self)
        session.commit()
        PeriodicTasksChanged.changed(self)

    def delete(self, session: Session, *args, **kwargs):
        session.delete(self)
        PeriodicTasksChanged.changed(self)

    @property
    def expires_(self):
        return self.expires or self.expire_seconds

    def __str__(self):
        fmt = '{0.name}: {{no schedule}}'
        if self.interval:
            fmt = '{0.name}: {0.interval}'
        if self.crontab:
            fmt = '{0.name}: {0.crontab}'
        if self.solar:
            fmt = '{0.name}: {0.solar}'
        if self.clocked:
            fmt = '{0.name}: {0.clocked}'
        return fmt.format(self)

    @property
    def scheduler(self):
        if self.interval:
            return self.interval
        if self.crontab:
            return self.crontab
        if self.solar:
            return self.solar
        if self.clocked:
            return self.clocked

    @property
    def schedule(self):
        return self.scheduler.schedule
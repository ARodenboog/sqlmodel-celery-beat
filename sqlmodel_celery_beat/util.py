from datetime import datetime
from zoneinfo import ZoneInfo

NEVER_CHECK_TIMEOUT = 9999999999

def nowfun() -> datetime:
    return datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))


def make_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo("UTC"))
    else:
        return ZoneInfo("UTC").localize(value)

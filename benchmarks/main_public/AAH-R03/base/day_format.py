import re
from datetime import datetime


def parse_day(value):
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("expected a calendar date")
    return datetime.strptime(value, "%Y-%m-%d").date()

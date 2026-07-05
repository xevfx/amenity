import re
import time as tm
from datetime import UTC, datetime, timedelta, timezone


def _timezone_from_string(timezone_str: str | None) -> timezone | None:
    if timezone_str is None:
        return None

    cleaned = timezone_str.strip().lower()
    if cleaned in {"utc", "gmt", "z"}:
        return UTC

    offset_match = re.match(r"^(?:utc|gmt)?([+-])(\d{1,2})(?::?(\d{2}))?$", cleaned)
    if not offset_match:
        raise ValueError("Invalid timezone format. Use UTC or UTC+5:45.")

    sign, hours_str, minutes_str = offset_match.groups()
    hours = int(hours_str)
    minutes = int(minutes_str or 0)
    if hours > 14 or minutes > 59:
        raise ValueError("Invalid timezone offset.")
    if hours == 14 and minutes:
        raise ValueError("UTC offsets cannot exceed 14 hours.")

    delta = timedelta(hours=hours, minutes=minutes)
    if sign == "-":
        delta = -delta
    return timezone(delta)


def _clock_time_to_seconds(time_str: str) -> int:
    clock_match = re.match(
        r"^(\d{1,2})(?::(\d{2}))?\s*([ap]m)?(?:\s+((?:utc|gmt|z)?[+-]\d{1,2}(?::?\d{2})?|utc|gmt|z))?$",
        time_str.lower(),
    )
    if not clock_match:
        raise ValueError("Invalid clock time format.")

    hour = int(clock_match.group(1))
    minute = int(clock_match.group(2) or 0)
    meridiem = clock_match.group(3)
    tzinfo = _timezone_from_string(clock_match.group(4))

    if minute > 59:
        raise ValueError("Minute must be between 0 and 59.")

    if meridiem:
        if hour < 1 or hour > 12:
            raise ValueError("Hour must be between 1 and 12 when using am/pm.")
        hour %= 12
        if meridiem == "pm":
            hour += 12
    elif hour > 23:
        raise ValueError("Hour must be between 0 and 23.")

    now = datetime.now(tzinfo) if tzinfo else datetime.now().astimezone()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    return int(target.timestamp() - tm.time())


def StringToTime(time_str: str) -> int:
    """
    Parse time string and return seconds.
    Supports formats: 1s, 1m, 1h, 1d, 1w, 1sec, 1min, 1hrs, etc.
    Also supports Discord timestamps like <t:1778847300:t>
    Also supports clock times like 2:25am, 14:25, 2:25am UTC, or 2:25am UTC+5:45.

    Args:
        time_str: Time string like "1h", "30m", "1d2h"

    Returns:
        Total seconds as integer

    Raises:
        ValueError: If format is invalid or unit is unknown
    """
    time_units = {
        "s": 1,
        "sec": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hrs": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
        "w": 604800,
        "week": 604800,
        "weeks": 604800,
    }

    cleaned = time_str.strip()

    timestamp_match = re.match(r"^<t:(\d+)(?::[tTdDfFR])?>$", cleaned)
    if timestamp_match:
        timestamp = int(timestamp_match.group(1))
        return timestamp - int(tm.time())

    normalized = cleaned.lower()
    clock_pattern = r"^\d{1,2}(?::\d{2})?\s*(?:[ap]m)?(?:\s+(?:(?:utc|gmt|z)?[+-]\d{1,2}(?::?\d{2})?|utc|gmt|z))?$"
    if re.match(clock_pattern, normalized):
        return _clock_time_to_seconds(normalized)

    valid_pattern = r"^(\d+(?:\.\d+)?\s*[a-zA-Z]+)+$"
    if not re.match(valid_pattern, normalized):
        raise ValueError("Invalid time format. Use format like '1h', '30m', '1d', or '2:25am UTC'")

    segment_pattern = r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)"
    segments = re.findall(segment_pattern, normalized)
    if not segments:
        raise ValueError("Invalid time format. Use format like '1h', '30m', '1d'")

    total_seconds = 0
    for number_str, unit in segments:
        if unit not in time_units:
            raise ValueError(f"Unknown time unit: {unit}. Use s, m, h, d, or w")
        total_seconds += float(number_str) * time_units[unit]

    return int(total_seconds)


def TimeToString(seconds: int) -> str:
    """
    Format seconds into a human-readable string.

    Args:
        seconds: Number of seconds

    Returns:
        Formatted string like "1d 2h 30m 15s"
    """
    if seconds <= 0:
        return "0s"

    parts = []

    weeks, seconds = divmod(seconds, 604800)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    if weeks:
        parts.append(f"{weeks}w")
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")

    return " ".join(parts)

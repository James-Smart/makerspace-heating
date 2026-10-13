"""Formatting of times and durations for status text."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo


def format_duration(delta: timedelta) -> str:
    """Format a duration as '40 s', '2 min', '1 min 40 s' or '1 h 30 min'.

    Seconds are dropped once the duration reaches an hour. Zero and negative
    durations format as '0 s'.
    """
    total = max(0, round(delta.total_seconds()))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} h")
    if minutes:
        parts.append(f"{minutes} min")
    if seconds and not hours:
        parts.append(f"{seconds} s")
    return " ".join(parts) or "0 s"


def format_clock(moment: datetime, zone: tzinfo | None) -> str:
    """Format a moment as HH:MM in the given time zone."""
    return moment.astimezone(zone).strftime("%H:%M")

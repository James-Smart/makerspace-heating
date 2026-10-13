"""Calendar tag matching, event parsing and pre-heat windows."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from .model import CalendarEvent, EventDemand

TAG_FORMAT = re.compile(r"^[a-z0-9-]+$")
"""A valid room tag: lowercase letters, digits and hyphens."""

_TAG_IN_TEXT = re.compile(r"(?<![a-z0-9_])#([a-z0-9-]+)(?![a-z0-9_-])", re.IGNORECASE)


@dataclass(frozen=True)
class UpcomingEvent:
    """A tagged event listed by the upcoming-heating sensor."""

    summary: str
    start: datetime
    end: datetime
    rooms: tuple[str, ...]


def find_tags(text: str) -> frozenset[str]:
    """Return the lowercase tags written as '#tag' in text, whole words only."""
    return frozenset(tag.lower() for tag in _TAG_IN_TEXT.findall(text))


def parse_events(
    raw: Iterable[Mapping[str, Any]], default_zone: tzinfo
) -> list[CalendarEvent]:
    """Convert calendar.get_events items into CalendarEvents.

    All-day events (date-only start or end), malformed items and events that do
    not end after they start are skipped. Naive times get default_zone.
    """
    events: list[CalendarEvent] = []
    for item in raw:
        start_text = str(item.get("start", ""))
        end_text = str(item.get("end", ""))
        if "T" not in start_text or "T" not in end_text:
            continue
        try:
            start = _aware(datetime.fromisoformat(start_text), default_zone)
            end = _aware(datetime.fromisoformat(end_text), default_zone)
        except ValueError:
            continue
        if end <= start:
            continue
        summary = str(item.get("summary") or "").strip()
        description = str(item.get("description") or "")
        events.append(
            CalendarEvent(
                summary=summary or "Untitled event",
                start=start,
                end=end,
                tags=find_tags(f"{summary}\n{description}"),
            )
        )
    return events


def _aware(moment: datetime, zone: tzinfo) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=zone)


def event_demand(
    events: Iterable[CalendarEvent], tag: str, preheat: timedelta, now: datetime
) -> EventDemand | None:
    """Return the event driving this tag's room right now, if any.

    A running event wins (the one ending latest is named). Otherwise an event
    whose pre-heat window has started is returned (the one starting soonest).
    """
    tagged = [event for event in events if tag in event.tags]
    running = [event for event in tagged if event.start <= now < event.end]
    if running:
        chosen = max(running, key=lambda event: event.end)
        return EventDemand(chosen.summary, chosen.start, chosen.end, running=True)
    warming = [event for event in tagged if event.start - preheat <= now < event.start]
    if warming:
        chosen = min(warming, key=lambda event: event.start)
        return EventDemand(chosen.summary, chosen.start, chosen.end, running=False)
    return None


def upcoming(
    events: Iterable[CalendarEvent], tag_names: Mapping[str, str], now: datetime
) -> tuple[list[UpcomingEvent], frozenset[str]]:
    """List tagged events that have not ended, and tags that match no room.

    tag_names maps each room tag to its room name.
    """
    listed: list[UpcomingEvent] = []
    unknown: set[str] = set()
    for event in sorted(events, key=lambda event: event.start):
        unknown |= event.tags.difference(tag_names)
        if event.end <= now:
            continue
        rooms = tuple(sorted(tag_names[tag] for tag in event.tags if tag in tag_names))
        if rooms:
            listed.append(UpcomingEvent(event.summary, event.start, event.end, rooms))
    return listed, frozenset(unknown)

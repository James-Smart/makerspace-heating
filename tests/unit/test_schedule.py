"""Tests for calendar tag matching, parsing and event windows."""

from datetime import timedelta

from custom_components.makerspace_heating.engine.model import (
    CalendarEvent,
    EventDemand,
)
from custom_components.makerspace_heating.engine.schedule import (
    TAG_FORMAT,
    event_demand,
    find_tags,
    parse_events,
    upcoming,
)
from tests.unit.helpers import LONDON, at

PREHEAT = timedelta(minutes=30)


def event(summary, start, end, *tags) -> CalendarEvent:
    return CalendarEvent(summary=summary, start=start, end=end, tags=frozenset(tags))


class TestFindTags:
    def test_finds_several_tags(self) -> None:
        assert find_tags("Pottery night #pottery #main") == {"pottery", "main"}

    def test_ignores_case(self) -> None:
        assert find_tags("#Workshop") == {"workshop"}

    def test_whole_words_only(self) -> None:
        assert find_tags("#workshop2") == {"workshop2"}
        assert find_tags("#workshop_x") == frozenset()
        assert find_tags("email#workshop") == frozenset()

    def test_hyphens_and_punctuation(self) -> None:
        assert find_tags("#wood-shop.") == {"wood-shop"}
        assert find_tags("(#workshop)") == {"workshop"}

    def test_tag_format(self) -> None:
        assert TAG_FORMAT.match("wood-shop2")
        assert not TAG_FORMAT.match("Workshop")
        assert not TAG_FORMAT.match("wood shop")


class TestParseEvents:
    def test_parses_timed_event_with_tags_from_description(self) -> None:
        raw = [
            {
                "start": "2026-10-13T19:00:00+01:00",
                "end": "2026-10-13T21:30:00+01:00",
                "summary": "Pottery night",
                "description": "Bring clay #pottery",
            }
        ]
        assert parse_events(raw, LONDON) == [
            event("Pottery night", at(19), at(21, 30), "pottery")
        ]

    def test_skips_all_day_events(self) -> None:
        raw = [{"start": "2026-10-13", "end": "2026-10-14", "summary": "Closed #workshop"}]
        assert parse_events(raw, LONDON) == []

    def test_naive_times_get_default_zone(self) -> None:
        raw = [{"start": "2026-10-13T19:00:00", "end": "2026-10-13T20:00:00", "summary": "A"}]
        [parsed] = parse_events(raw, LONDON)
        assert parsed.start == at(19)

    def test_skips_malformed_and_backwards_items(self) -> None:
        raw = [
            {"start": "not a time T", "end": "2026-10-13T20:00:00+01:00"},
            {"start": "2026-10-13T20:00:00+01:00", "end": "2026-10-13T19:00:00+01:00"},
            {"summary": "no times"},
        ]
        assert parse_events(raw, LONDON) == []

    def test_empty_summary_gets_a_name(self) -> None:
        raw = [
            {
                "start": "2026-10-13T19:00:00+01:00",
                "end": "2026-10-13T20:00:00+01:00",
                "description": "#main",
            }
        ]
        [parsed] = parse_events(raw, LONDON)
        assert parsed.summary == "Untitled event"
        assert parsed.tags == {"main"}


class TestEventDemand:
    def test_running_event(self) -> None:
        events = [event("Pottery", at(19), at(21), "pottery")]
        assert event_demand(events, "pottery", PREHEAT, at(20)) == EventDemand(
            "Pottery", at(19), at(21), running=True
        )

    def test_preheat_window(self) -> None:
        events = [event("Pottery", at(19), at(21), "pottery")]
        demand = event_demand(events, "pottery", PREHEAT, at(18, 30))
        assert demand is not None and demand.running is False
        assert event_demand(events, "pottery", PREHEAT, at(18, 29)) is None

    def test_other_rooms_events_ignored(self) -> None:
        events = [event("Pottery", at(19), at(21), "pottery")]
        assert event_demand(events, "main", PREHEAT, at(20)) is None

    def test_end_is_exclusive(self) -> None:
        events = [event("Pottery", at(19), at(21), "pottery")]
        assert event_demand(events, "pottery", PREHEAT, at(21)) is None

    def test_running_beats_preheat_and_latest_end_is_named(self) -> None:
        events = [
            event("Short", at(19), at(20), "w"),
            event("Long", at(19), at(22), "w"),
            event("Next", at(20), at(21), "w"),
        ]
        demand = event_demand(events, "w", PREHEAT, at(19, 45))
        assert demand is not None and demand.summary == "Long"

    def test_back_to_back_events_are_continuous(self) -> None:
        events = [event("A", at(19), at(20), "w"), event("B", at(20), at(21), "w")]
        assert event_demand(events, "w", timedelta(0), at(19, 59, 59)).summary == "A"
        assert event_demand(events, "w", timedelta(0), at(20)).summary == "B"

    def test_zero_preheat(self) -> None:
        events = [event("A", at(19), at(20), "w")]
        assert event_demand(events, "w", timedelta(0), at(18, 59)) is None


class TestUpcoming:
    def test_lists_tagged_future_events_in_start_order(self) -> None:
        events = [
            event("Later", at(20), at(21), "main"),
            event("Sooner", at(18), at(19), "workshop", "main"),
            event("Untagged", at(17), at(18)),
            event("Past", at(9), at(10), "main"),
        ]
        listed, unknown = upcoming(
            events, {"workshop": "Workshop", "main": "Main Space"}, at(12)
        )
        assert [(item.summary, item.rooms) for item in listed] == [
            ("Sooner", ("Main Space", "Workshop")),
            ("Later", ("Main Space",)),
        ]
        assert unknown == frozenset()

    def test_reports_unknown_tags(self) -> None:
        listed, unknown = upcoming(
            [event("Typo", at(18), at(19), "wrokshop")], {"workshop": "Workshop"}, at(12)
        )
        assert listed == []
        assert unknown == {"wrokshop"}

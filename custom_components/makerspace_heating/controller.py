"""Connects the decision engine to Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    BUTTON_DEBOUNCE,
    CALENDAR_REFRESH,
    CALENDAR_TIMEOUT,
    CALENDAR_WINDOW,
    CONF_BOILER,
    CONF_CALENDAR,
    DOMAIN,
    EVENT_LOGBOOK_ENTRY,
    HANDOVER_WINDOW,
    MODE_ACTIVE,
    MODE_PAUSED,
    MODE_SHADOW,
    NAME,
    RESEND_INTERVAL,
    SAVE_DELAY,
    SUBENTRY_ROOM,
    TICK_INTERVAL,
)
from .engine.engine import BoostError, Decision, Engine
from .engine.model import CalendarEvent, FaultKey, RoomInputs, Snapshot
from .engine.persist import STORE_VERSION, dump, dump_events, load_events, restore
from .engine.schedule import UpcomingEvent, parse_events
from .engine.schedule import upcoming as list_upcoming
from .engine.timefmt import format_duration
from .options import RoomConfig, control_mode, global_settings, notify_targets, room_config

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

_PREFIXES = {MODE_SHADOW: "Shadow: ", MODE_PAUSED: "Paused: "}

_EXPECTED_CALENDAR_ERRORS = (HomeAssistantError, probatio.Invalid, KeyError, TypeError)
"""Calendar fetch errors that are logged without a traceback."""

_HANDOVER_AT = "handover_at"
"""Saved-state key: when an unload or reload saved it (absent after a restart)."""


def _is_handover(saved: Mapping[str, Any], now: datetime) -> bool:
    """Whether saved state was left by a reload moments ago rather than a restart."""
    try:
        handover_at = datetime.fromisoformat(saved[_HANDOVER_AT])
        return now - handover_at <= HANDOVER_WINDOW
    except (KeyError, TypeError, ValueError):
        return False


def read_temperature(state: State | None) -> float | None:
    """Return a sensor state's temperature in °C, or None if unusable."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit in (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.KELVIN):
        value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
    return value


def read_switch(state: State | None) -> bool | None:
    """Return True/False for on/off, or None when unavailable or unknown."""
    if state is None:
        return None
    if state.state == STATE_ON:
        return True
    if state.state == STATE_OFF:
        return False
    return None


def is_press(old: State | None, new: State | None) -> bool:
    """Whether a button entity's state change is a new physical press.

    Event entities count a new timestamp, except when coming back from
    unavailable (the old timestamp is restored) or for a release (btn_up).
    Binary sensors count off -> on.
    """
    if old is None or new is None or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return False
    if new.domain == "binary_sensor":
        return old.state == STATE_OFF and new.state == STATE_ON
    if old.state == STATE_UNAVAILABLE or old.state == new.state:
        return False
    return new.attributes.get("event_type") != "btn_up"


async def describe_origin(hass: HomeAssistant, context: Context | None) -> str:
    """Describe who started a boost from a service call's context (spec §7.4)."""
    if context is not None and context.user_id:
        user = await hass.auth.async_get_user(context.user_id)
        if user is not None and user.name:
            return f"Dashboard ({user.name})"
        return "Dashboard"
    return "Automation/script"


class HeatingController:
    """Runs the engine against live Home Assistant state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.settings = global_settings(entry.options)
        self.mode = control_mode(entry.options)
        self.boiler_entity: str = entry.data[CONF_BOILER]
        self.calendar_entity: str = entry.data[CONF_CALENDAR]
        self.rooms: dict[str, RoomConfig] = {
            subentry_id: room_config(subentry_id, subentry.data)
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_ROOM
        }
        self.engine = Engine(
            self.settings, [room.settings for room in self.rooms.values()], dt_util.now()
        )
        self.decision: Decision | None = None
        self.events: tuple[CalendarEvent, ...] = ()
        self.calendar_failing_since: datetime | None = None
        self.upcoming: list[UpcomingEvent] = []
        self.unknown_tags: frozenset[str] = frozenset()
        self._tag_names = {room.settings.tag: room.settings.name for room in self.rooms.values()}
        self._button_rooms = {
            room.button: room_id for room_id, room in self.rooms.items() if room.button
        }
        self._owned_names = {self.boiler_entity: "Boiler"} | {
            room.valve: f"{room.settings.name} valve" for room in self.rooms.values()
        }
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._listeners: list[Callable[[], None]] = []
        self._unsubscribe: list[Callable[[], None]] = []
        self._last_press: dict[str, datetime] = {}
        self._desired: dict[str, bool] = {}
        self._sent: dict[str, tuple[bool, datetime]] = {}
        self._fetching = False
        self._seen_unknown_tags: set[str] = set()
        self._closed = False
        self.notify_entities, self.notify_actions = notify_targets(entry.options)
        self._raised: dict[FaultKey, str] = {}

    @property
    def active(self) -> bool:
        """Whether the integration is switching devices."""
        return self.mode == MODE_ACTIVE

    def status_prefix(self) -> str:
        """'Shadow: ' or 'Paused: ' when not in control, otherwise ''."""
        return _PREFIXES.get(self.mode, "")

    async def async_setup(self) -> None:
        """Restore state, subscribe to inputs and make the first decision."""
        now = dt_util.now()
        saved = await self._store.async_load()
        if saved:
            restore(self.engine, saved, now, handover=_is_handover(saved, now))
            self.events = load_events(saved.get("events"))
        tracked = [self.boiler_entity, self.calendar_entity]
        for room in self.rooms.values():
            tracked += [room.valve, room.temperature]
            if room.button:
                tracked.append(room.button)
        self._unsubscribe += [
            async_track_state_change_event(self.hass, tracked, self._on_state_change),
            async_track_time_interval(self.hass, self._on_tick, TICK_INTERVAL),
            async_track_time_interval(self.hass, self._on_calendar_timer, CALENDAR_REFRESH),
            async_at_started(self.hass, self._on_started),
        ]
        self.async_step()

    async def async_shutdown(self, *, handover: bool = True) -> None:
        """Stop listening and save state (on unload or reload only). Safe to call twice.

        With handover the saved state is marked so that a controller set up
        straight after (a reload) carries on with a running boiler.
        """
        self._closed = True
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        for key in self._raised:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(key))
        self._raised.clear()
        data = self._saved_state()
        if handover:
            data[_HANDOVER_AT] = dt_util.now().isoformat()
        await self._store.async_save(data)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call listener after every decision. Returns an unsubscribe function."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_boost(self, room_id: str, length: timedelta | None, origin: str) -> None:
        """Start a boost (default length when length is None)."""
        try:
            self.engine.boost(
                room_id,
                length if length is not None else self.settings.default_boost,
                origin,
                dt_util.now(),
            )
        except BoostError as err:
            raise ServiceValidationError(str(err)) from err
        self.async_step()

    @callback
    def async_press_boost(self, room_id: str, origin: str) -> None:
        """Dashboard Boost button: a button press that never shortens a boost (spec §3.4)."""
        if not self.engine.room_state[room_id].enabled:
            raise ServiceValidationError("Room is off")
        self.engine.press(room_id, dt_util.now(), origin)
        self.async_step()

    @callback
    def async_cancel_boost(self, room_id: str, origin: str) -> None:
        """End a room's boost early."""
        self.engine.cancel_boost(room_id, origin, dt_util.now())
        self.async_step()

    @callback
    def async_set_enabled(self, room_id: str, enabled: bool) -> None:
        """Turn a room on or off."""
        self.engine.set_enabled(room_id, enabled)
        self.async_step()

    @callback
    def async_step(self) -> None:
        """Read inputs, run the engine and act on the decision."""
        if self._closed:
            return
        now = dt_util.now()
        states = self.hass.states
        rooms: dict[str, RoomInputs] = {}
        for room_id, room in self.rooms.items():
            valve = read_switch(states.get(room.valve))
            if not self.active and valve is not None:
                valve = self.engine.commanded_valve(room_id)
            button = states.get(room.button) if room.button else None
            rooms[room_id] = RoomInputs(
                temperature=read_temperature(states.get(room.temperature)),
                valve_on=valve,
                button_available=room.button is None
                or (button is not None and button.state != STATE_UNAVAILABLE),
            )
        snapshot = Snapshot(
            now=now,
            rooms=rooms,
            boiler_available=read_switch(states.get(self.boiler_entity)) is not None,
            events=self.events,
            calendar_failing_since=self.calendar_failing_since,
            check_valves=self.active,
        )
        decision = self.decision = self.engine.step(snapshot)
        self.upcoming, self.unknown_tags = list_upcoming(self.events, self._tag_names, now)
        if self.active:
            self._apply(decision, now)
        for line in decision.log:
            self._log_entry(line.name, line.message)
        self._update_alerts(decision.faults)
        self._store.async_delay_save(self._saved_state, SAVE_DELAY)
        for listener in list(self._listeners):
            listener()

    async def async_refresh_calendar(self) -> None:
        """Fetch the next 24 hours of events from the calendar."""
        if self._fetching or self._closed:
            return
        self._fetching = True
        now = dt_util.now()
        try:
            async with asyncio.timeout(CALENDAR_TIMEOUT.total_seconds()):
                response = await self.hass.services.async_call(
                    "calendar",
                    "get_events",
                    {
                        ATTR_ENTITY_ID: self.calendar_entity,
                        "start_date_time": now,
                        "end_date_time": now + CALENDAR_WINDOW,
                    },
                    blocking=True,
                    return_response=True,
                )
            raw = response[self.calendar_entity]["events"]  # type: ignore[index]
            events = parse_events(raw, dt_util.get_default_time_zone())
        except Exception as err:  # noqa: BLE001 - providers' errors pass straight through
            if self._closed:
                return
            if self.calendar_failing_since is None:
                self.calendar_failing_since = now
            if isinstance(err, TimeoutError):
                _LOGGER.warning(
                    "Could not read calendar %s: no answer within %s",
                    self.calendar_entity,
                    format_duration(CALENDAR_TIMEOUT),
                )
            else:
                _LOGGER.warning(
                    "Could not read calendar %s: %s",
                    self.calendar_entity,
                    err,
                    exc_info=not isinstance(err, _EXPECTED_CALENDAR_ERRORS),
                )
        else:
            if self._closed:
                return
            self.calendar_failing_since = None
            self.events = tuple(events)
            self._warn_unknown_tags(now)
        finally:
            self._fetching = False
        self.async_step()

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        old_state, new_state = event.data["old_state"], event.data["new_state"]
        room_id = self._button_rooms.get(entity_id)
        if room_id is not None and is_press(old_state, new_state):
            self._handle_press(room_id)
        if entity_id == self.calendar_entity:
            self.entry.async_create_task(
                self.hass, self.async_refresh_calendar(), "makerspace heating calendar"
            )
        if self.active and entity_id in self._owned_names:
            self._note_manual_change(entity_id, old_state, new_state)
        self.async_step()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self.async_step()

    async def _on_calendar_timer(self, _now: datetime) -> None:
        await self.async_refresh_calendar()

    async def _on_started(self, _hass: HomeAssistant) -> None:
        await self.async_refresh_calendar()

    @callback
    def _handle_press(self, room_id: str) -> None:
        now = dt_util.now()
        last = self._last_press.get(room_id)
        if last is not None and now - last < BUTTON_DEBOUNCE:
            return
        self._last_press[room_id] = now
        self.engine.press(room_id, now)

    @callback
    def _apply(self, decision: Decision, now: datetime) -> None:
        for room_id, room_decision in decision.rooms.items():
            self._ensure(self.rooms[room_id].valve, room_decision.valve_on, now)
        self._ensure(self.boiler_entity, decision.boiler_on, now)

    @callback
    def _ensure(self, entity_id: str, on: bool, now: datetime) -> None:
        """Send a command if the device is not already in the wanted state."""
        self._desired[entity_id] = on
        if read_switch(self.hass.states.get(entity_id)) in (None, on):
            return
        sent = self._sent.get(entity_id)
        if sent is not None and sent[0] == on and now - sent[1] < RESEND_INTERVAL:
            return
        self._sent[entity_id] = (on, now)
        domain = entity_id.split(".", 1)[0]
        self._call_task(domain, SERVICE_TURN_ON if on else SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id})

    @callback
    def _note_manual_change(
        self, entity_id: str, old_state: State | None, new_state: State | None
    ) -> None:
        """Log when someone changes an owned switch away from what we wanted."""
        desired = self._desired.get(entity_id)
        before, after = read_switch(old_state), read_switch(new_state)
        if desired is None or before != desired or after is None or after == desired:
            return
        self._sent.pop(entity_id, None)
        self._log_entry(
            self._owned_names[entity_id],
            f"was switched {'on' if after else 'off'} by hand; "
            f"switching it back {'on' if desired else 'off'}",
        )

    @callback
    def _update_alerts(self, raised: dict[FaultKey, str]) -> None:
        """Create and remove Repairs issues and send notifications on changes."""
        for key, message in raised.items():
            if key in self._raised:
                continue
            self._raised[key] = message
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id(key),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="fault",
                translation_placeholders={"message": message},
            )
            self._log_entry(NAME, f"fault: {message}")
            self._notify(f"Heating fault: {message}")
        for key in [key for key in self._raised if key not in raised]:
            message = self._raised.pop(key)
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(key))
            self._log_entry(NAME, f"fault cleared: {message}")
            self._notify(f"Heating fault cleared: {message}")

    @staticmethod
    def _issue_id(key: FaultKey) -> str:
        return f"{key.scope}_{key.kind}"

    @callback
    def _notify(self, message: str) -> None:
        for entity_id in self.notify_entities:
            self._call_task(
                "notify",
                "send_message",
                {ATTR_ENTITY_ID: entity_id, "message": message, "title": NAME},
            )
        for action in self.notify_actions:
            domain, _, service = action.partition(".")
            self._call_task(domain, service, {"message": message, "title": NAME})

    @callback
    def _warn_unknown_tags(self, now: datetime) -> None:
        _, unknown = list_upcoming(self.events, self._tag_names, now)
        for tag in sorted(unknown - self._seen_unknown_tags):
            _LOGGER.warning("A calendar event uses #%s, which matches no room", tag)
        self._seen_unknown_tags |= unknown

    @callback
    def _saved_state(self) -> dict[str, Any]:
        """The engine state plus the calendar event cache, without a handover mark."""
        return dump(self.engine) | {"events": dump_events(self.events)}

    @callback
    def _log_entry(self, name: str, message: str) -> None:
        self.hass.bus.async_fire(
            EVENT_LOGBOOK_ENTRY, {"name": name, "message": message, "domain": DOMAIN}
        )

    @callback
    def _call_task(self, domain: str, service: str, data: dict[str, Any]) -> None:
        self.entry.async_create_task(
            self.hass,
            self._call(domain, service, data),
            f"makerspace heating {domain}.{service}",
            eager_start=False,
        )

    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
        except (HomeAssistantError, probatio.Invalid) as err:
            _LOGGER.warning("Could not call %s.%s: %s", domain, service, err)

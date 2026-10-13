# Makerspace Heating — Design

**Date:** 2026-10-13
**Status:** Draft for review
**Integration:** Makerspace Heating (`makerspace_heating`), a Home Assistant custom integration

## 1. Purpose

The makerspace heating is currently controlled by a mix of schedules, automations and
generic thermostats. This integration replaces them with a single component that owns
all heating decisions.

### Problems it must solve

| Problem today | What the integration must do |
|---|---|
| Hard to see why a room (or the boiler) is or isn't heating | Every room and the boiler has a plain-English status naming the reason; decisions are written to the logbook |
| Schedules, buttons and thermostats conflict | One rule set, in one place, covered by scenario tests |
| Adding or changing a room means editing several automations and helpers | Adding a room is one form in the UI; no automations |
| Missing features | Pre-heat before events, frost protection, fault alerts, runtime stats |

### Success criteria

- For any room at any moment, its status sensor and the logbook explain what it is
  doing and why, without reading automations.
- Every rule in this document has at least one scenario test in the decision engine.
- A new room can be added through the UI with no YAML or automations.
- Pre-heat, frost protection, fault alerts and runtime stats work as described below.
- The integration runs in Shadow mode alongside the existing setup and produces the
  expected decisions before it is switched to Active.

## 2. Physical setup

- **Rooms.** Each room has:
  - a Shelly relay driving a thermal actuator on the radiator valve (relay on = valve open);
  - a temperature sensor;
  - optionally, a momentary "demand heat" push button wired to a Shelly input.
    The main event space has no button yet.
- **Boiler.** One Shelly relay gives the boiler an on/off call for heat.
- **Home Assistant.** Version 2025.6 or newer; integration installed from a GitHub
  repository via HACS. Shellys are connected through the official Shelly integration.

## 3. Room behaviour

### 3.1 Demand sources

Each room has three sources of demand. Each source, when active, asks for a temperature.

| Source | Active when | Asks for | Default |
|---|---|---|---|
| **Event** | A tagged calendar event is running, or starts within the room's pre-heat lead time (§5) | Room's event target | 20 °C |
| **Boost** | A boost is running (§3.4) | Room's boost target | 19 °C |
| **Frost** | Always | Room's frost target | 7 °C |

**The room's target is the highest target among its active sources.** No source can
lower another source's demand:

- A boost during an event does not shorten the event.
- When an event ends, a boost that is still running continues until its own end time.

### 3.2 Valve control

The room's thermostat logic uses a single margin (default 0.3 °C, admin setting):

- Open the valve when temperature ≤ target − margin.
- Close the valve when temperature ≥ target.
- Between the two, keep the current state.
- On startup, the valve starts closed unless temperature ≤ target − margin.

A room is **calling for heat** when its thermostat logic wants the valve open. Valves can
also be held open for boiler protection (§4.3); holding a valve open does not count as
calling.

### 3.3 Room on/off

Each room has an on/off switch (`switch.<room>_enabled`).

- **On:** normal behaviour.
- **Off:** boosts and events are ignored, but frost protection still applies. Button
  presses are ignored and logged as "ignored: room off". Dashboard or action boosts on an
  Off room fail with the error "Room is off".

### 3.4 Boosts

- A boost has an end time, a length and a record of who started it (§7.4).
- **Physical button:** starts a boost of the default length (default 30 min). Pressing
  while a boost is running sets end = max(current end, now + default length), so a press
  restarts the 30 minutes but never shortens a longer dashboard boost.
- **Dashboard and action boosts:** an explicit choice. They start or replace the boost
  with end = now + chosen length, even if that is earlier than the current end. The
  length must be > 0 and ≤ the maximum boost length (§6.1).
- Changing settings does not alter running boosts.

### 3.5 Button input

The room's button may be:

- an `event` entity: any event counts as a press;
- a `binary_sensor`: an off → on transition counts as a press.

Presses within 2 s of the previous press are ignored. This stops a Shelly that reports
several event types for one physical press (e.g. `btn_down` and `single_push`) from
counting it more than once.

### 3.6 Room status

Each room exposes a status sensor in plain English. It names the winning source, plus
"heating" or "at temperature" where relevant. Examples:

- "Pre-heating for Pottery night (starts 19:00)"
- "Event: Pottery night until 21:30, heating"
- "Boost until 14:32, at temperature"
- "Frost protection"
- "Off (frost protection only)"
- "Fault: no temperature reading"

The sensor's attributes carry structured detail: winning source, target, boost end and
who started it, current event name and end, and any active fault.

### 3.7 Main event space

The main event space is an ordinary room with no button configured. It can be boosted
from the dashboard.

## 4. Boiler behaviour

### 4.1 Basic rule

The boiler should be on when at least one room is calling for heat. §4.2 adds conditions
to this rule.

- Rooms that are Off can still call, but only for frost protection.
- Rooms with a *heating fault* (§7.5) never call.

### 4.2 Protections

All of these are admin settings.

| Protection | Rule | Default |
|---|---|---|
| **Wait for valves** | The boiler turns on only once at least one calling room's valve has reported *on* for at least this long. 0 disables the wait. | 3 min |
| **Minimum run** | Once on, the boiler stays on for at least this long. | 5 min |
| **Minimum rest** | Once off, the boiler stays off for at least this long, even if rooms are calling. | 5 min |
| **Keep-open after stop** | See §4.3. | 3 min |

While the boiler is resting, calling rooms' valves still open, so the wait-for-valves time
counts down during the rest period.

### 4.3 Valves follow the boiler

- While the boiler is on, at least one valve is always open. If no room is calling (the
  boiler is holding for its minimum run), the valves of the room(s) that were calling
  last stay open.
- After the boiler turns off, those held valves stay open for the keep-open time, then
  close unless their room is calling.
- This means a room may overshoot its target slightly, by a few minutes' worth of heat.
  In exchange, hot water always has somewhere to go, including during the boiler's own
  pump overrun.

### 4.4 Boiler status

The boiler status sensor reads, for example:

- "Off: no rooms calling"
- "Waiting for valves: Workshop (1 min 40 s)"
- "On: Workshop, Main Space calling"
- "On: holding for minimum run time"
- "Off: resting (2 min left)"

A `binary_sensor` (boiler demand) gives a simple on/off for graphs.

## 5. Calendar, tags and pre-heat

### 5.1 Calendar and tags

- An admin selects one calendar entity in the shared settings.
- Each room has a unique tag, e.g. `workshop`. A tag is lowercase letters, digits and
  hyphens.
- An event heats a room if its title or description contains `#<tag>`:
  - case-insensitive;
  - whole word only: the character after the tag must not be a letter, digit, hyphen or
    underscore. So `#workshop2` does not match `#workshop`.
- One event may carry several tags and heats every matching room.
- **All-day events are ignored.**

### 5.2 Pre-heat

- A room's event source is active from (event start − pre-heat lead time) until event end.
- Lead time is a fixed number of minutes per room (default 30, 0 allowed).
- Overlapping or back-to-back events merge into one continuous heating period.
- When several events are active, the status names the one ending latest.

### 5.3 Fetching

- The integration fetches events from now to now + 24 h using Home Assistant's
  `calendar.get_events` action. It fetches every 5 minutes, and immediately when the
  calendar entity's state changes.
- Event start and end transitions are evaluated against the cached list on every engine
  tick (§8.1), so heating starts and stops on time between fetches.

### 5.4 Calendar failure

- If a fetch fails, the last successful event list stays in use.
- Buttons and dashboard boosts are unaffected.
- If fetching has failed for 30 minutes (admin setting), a calendar fault is raised (§7.1).

### 5.5 Upcoming heating sensor

A hub-level sensor shows the next tagged event (its name and start). Its attributes list:

- all tagged events in the next 24 h, with their rooms;
- **unknown tags**: `#words` found in calendar events that match no room. These are
  informational, not alerts, so a typo such as `#wrokshop` is visible on a dashboard.
  Unknown tags are also logged.

## 6. Configuration

Configuration is done through the UI, which is admin-only in Home Assistant. Dashboards
are reachable by any logged-in user, so nothing that would get round a limit is exposed
as an entity.

### 6.1 Main entry (one per Home Assistant instance)

**Set up / reconfigure:** boiler switch, calendar entity.

**Options:**

| Setting | Default |
|---|---|
| Control mode: Active / Shadow / Paused (§8.3) | Shadow |
| Default boost length | 30 min |
| Maximum boost length | 2 h |
| Boost dropdown step | 30 min |
| Valve margin | 0.3 °C |
| Wait for valves (0 = off) | 3 min |
| Boiler minimum run | 5 min |
| Boiler minimum rest | 5 min |
| Keep-open after stop | 3 min |
| Fault delay | 5 min |
| Heating-but-not-warming: duration / minimum rise | 60 min / 0.5 °C |
| Calendar failure threshold | 30 min |
| Notify targets: notify entities and/or legacy notify actions (e.g. `notify.mobile_app_x`) | none |

### 6.2 Room sub-entries (one per room, reconfigurable)

| Field | Notes |
|---|---|
| Name | Used for the device and entity names |
| Tag | Unique across rooms; validated format |
| Valve switch | `switch` or `input_boolean`; must not be used by another room or as the boiler |
| Temperature sensor | `sensor` with temperature device class |
| Button | Optional; `event` or `binary_sensor` |
| Boost target | Default 19 °C |
| Event target | Default 20 °C |
| Frost target | Default 7 °C; must be ≤ boost and event targets |
| Pre-heat lead time | Default 30 min |

Targets are limited to 5–30 °C. Changing any setting reloads the integration; running
boosts and other saved state are kept (§8.2).

## 7. Entities, actions, alerts and stats

### 7.1 Per room (one device per room)

| Entity | Purpose |
|---|---|
| `sensor.<room>_status` | Plain-English status (§3.6) |
| `sensor.<room>_target_temperature` | Current effective target (°C) |
| `binary_sensor.<room>_heating` | Room is calling for heat |
| `binary_sensor.<room>_problem` | Room has an active fault (device class `problem`) |
| `sensor.<room>_boost_ends` | Timestamp; unknown when no boost |
| `button.<room>_boost` | Default-length boost, same as the physical button |
| `select.<room>_boost_for` | Options: "Off", then multiples of the step up to the maximum (e.g. "30 min", "1 h", "1 h 30 min", "2 h"). Choosing a length starts or restarts a boost of that length; "Off" ends the boost. While a boost runs, shows its length; otherwise "Off". If a running boost's length is no longer an option (after a settings change), the state is unknown until it ends. |
| `switch.<room>_enabled` | Room on/off (§3.3) |
| `sensor.<room>_heating_time_today` | Time spent calling today (h), `total_increasing`, resets at local midnight |
| `sensor.<room>_boosts_today` | Boosts started today, `total_increasing`, resets at local midnight |

The room's existing temperature sensor is used directly; it is not duplicated.

### 7.2 Hub (one device)

| Entity | Purpose |
|---|---|
| `sensor.boiler_status` | Plain-English boiler status (§4.4); prefixed "Shadow:" or "Paused:" when not Active |
| `binary_sensor.boiler_demand` | Boiler on/off decision |
| `binary_sensor.heating_problem` | Any hub-level fault: boiler or calendar |
| `sensor.boiler_on_time_today` | Hours on today, `total_increasing` |
| `sensor.boiler_starts_today` | Starts today, `total_increasing` |
| `sensor.upcoming_heating` | §5.5 |

### 7.3 Action

`makerspace_heating.boost`

- **Target:** one or more room devices.
- **Field:** `duration`, optional; defaults to the default boost length.
- **Errors:**
  - a duration ≤ 0 or above the maximum raises a validation error;
  - an Off room raises "Room is off".

### 7.4 Who started a boost

Each boost records its origin, shown in the room status attributes and the logbook:

- "Button";
- "Dashboard (<user name>)", from the service call's user context;
- "Automation/script".

### 7.5 Faults and alerts

There are two kinds of fault:

- **Heating faults** change behaviour: the room stops calling and its status shows the
  fault.
- **Alert-only faults** change nothing: they appear in the status attributes and raise
  alerts.

| Fault | Kind | Effect |
|---|---|---|
| Room temperature sensor unavailable/unknown | Heating | Valve closes; room status shows the fault |
| Valve switch unavailable, or not matching the command for 3 consecutive ticks | Heating | Room stops calling, so the boiler will not fire for an unconfirmed valve |
| Boiler switch unavailable | Alert-only (hub) | Rooms keep deciding so statuses still show intended behaviour |
| Calendar fetch failing beyond threshold | Alert-only (hub) | §5.4 |
| Button unavailable | Alert-only (room) | Dashboard boosts still work |
| Heating but not warming: room calling continuously for 60 min with < 0.5 °C rise since it started calling | Alert-only (room) | Clears when the room stops calling or the rise is reached |

**Alert timing:**

- A fault must persist for the fault delay (default 5 min) before it is raised.
- No faults are raised in the first 2 minutes after startup.

**When raised:**

- The relevant problem sensor turns on.
- A Home Assistant **Repairs** issue is created. It is removed automatically when the
  fault clears.
- Each configured notify target receives one message when the fault is raised and one
  when it clears.

### 7.6 Logbook

The integration writes logbook entries for:

- boost start, with its origin;
- boost end;
- boost ignored because the room is Off;
- boiler on/off, with its reason;
- manual overrides corrected (§8.1);
- faults raised and cleared.

## 8. Runtime

### 8.1 Evaluation and device ownership

- The engine evaluates on any relevant input state change, and on a 30-second tick.
- In Active mode, the integration owns the valve and boiler switches. If a switch's
  state differs from the decision, it re-sends the command each tick and logs that it
  corrected a manual change. Three consecutive mismatches on a valve raise the valve
  fault.

### 8.2 Persistence

The following state is saved with Home Assistant's storage helper and restored on startup
and on reload:

- running boosts: end time, length, origin;
- room on/off;
- boiler last on and last off times;
- held valves;
- today's counters, with their date.

A boost that expired while Home Assistant was down simply ends. On startup, the normal
protections (wait for valves, minimum rest) apply before the boiler fires.

### 8.3 Control modes (admin setting)

| Mode | Behaviour |
|---|---|
| **Shadow** (default for a new install) | Full evaluation, statuses, stats, logbook and alerts, but no commands are sent to any Shelly. Valve-mismatch faults are not raised. |
| **Active** | Full control. The README includes a checklist of the old automations, schedules and generic thermostats to disable first. |
| **Paused** | As Shadow, for maintenance or testing; statuses are prefixed "Paused:". |

### 8.4 Home Assistant availability

No device-level fallback or timeout is configured on the Shellys. If Home Assistant is
down, devices stay in their last state. This was a deliberate choice; fault alerts cover
devices that drop off from Home Assistant's side.

## 9. Code structure

```
custom_components/makerspace_heating/
  __init__.py          setup, unload, reload
  manifest.json
  const.py
  engine/              pure decision logic, no Home Assistant imports; time passed in
    model.py           dataclasses: settings, inputs, decisions, statuses
    room.py            demand sources, target, valve margin, room on/off
    boiler.py          wait for valves, minimum run/rest, valves follow boiler
    schedule.py        tag parsing, pre-heat windows, event merging
    faults.py          fault detection and debounce, heating-but-not-warming
  controller.py        collects inputs from HA, runs the engine, applies outputs,
                       owns timers, calendar fetching, persistence, logbook, alerts
  config_flow.py       main entry, options, room sub-entries
  sensor.py
  binary_sensor.py
  button.py
  select.py
  switch.py
  services.yaml        makerspace_heating.boost
  strings.json
  translations/en.json
tests/
  engine/              fast scenario tests for every rule
  ...                  HA tests: config flow, entity creation, boost limits, restore
dashboards/
  makerspace-heating.yaml   example dashboard, built-in cards only, one block per room to copy
hacs.json
README.md
```

**Engine interface:** a pure function, or a small stateful object with an explicit
`step(now, inputs) -> decisions` method. It takes a snapshot of all inputs (time,
temperatures, availability, presses since last step, cached events, settings, saved
state) and returns valve commands, boiler command, statuses, faults and updated state.
The controller is the only code that touches Home Assistant.

## 10. Testing

- **Engine:** pytest scenario tests with explicit timestamps. Examples:
  - press at 10:00, press again at 10:20 → boost ends 10:50;
  - event at 10:30 with 30 min pre-heat → event source active from 10:00;
  - boost outlasting an event;
  - valve margin behaviour;
  - wait for valves; minimum run and rest; keep-open after stop;
  - each fault type and its debounce;
  - heating but not warming;
  - tag matching edge cases;
  - all-day events ignored;
  - room Off still gets frost protection.
- **Home Assistant layer:** `pytest-homeassistant-custom-component`:
  - config flow and sub-entries, including validation;
  - entities created per room;
  - select options derived from settings;
  - `boost` action enforces the maximum;
  - persistence across restart;
  - Shadow mode sends no commands.
- **CI:** GitHub Actions running tests, `hassfest` and HACS validation on every push.

## 11. Out of scope for v1

- Learned or adaptive pre-heat
- Local fallback on the Shellys or device-level timeouts
- "Boost all rooms" control
- Long-press-to-cancel on the physical button
- Occupancy, window or weather inputs
- Multiple calendars
- A climate (thermostat) entity, left out so that targets stay admin-only

## 12. To check on site

These change defaults, not the design.

- Is there an automatic bypass valve? If yes, wait-for-valves and keep-open can be shortened or set to 0.
- How long do the valve actuators take to open? This sets the wait-for-valves time.
- Are the button Shelly inputs in button mode (`event` entities) or switch mode (`binary_sensor`)?
- How often do the room temperature sensors report? This affects how quickly the valve margin reacts.

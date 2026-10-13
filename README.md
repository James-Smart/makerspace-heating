# Makerspace Heating

A Home Assistant integration that makes every heating decision for the space in
one place. It controls the room valves and the boiler, and explains why each room
and the boiler are doing what they are.

Design: `docs/superpowers/specs/2026-10-13-makerspace-heating-design.md`.

## What it does

- **Rooms** heat to the highest of three demands:
  - **Event**: a tagged calendar event, including pre-heat before it starts.
  - **Boost**: a button press or a dashboard boost.
  - **Frost**: always on, so rooms never drop below the frost target.
- **The boiler** runs only while a room calls for heat and its valve has
  reported open for the *wait for valves* time. It keeps at least one valve open
  while running and for a few minutes after stopping. Its minimum run and rest
  times hold within a run and across settings and room changes, so it does not
  short-cycle. A Home Assistant restart while the boiler is on gives it one
  minimum-rest period.
- **Faults**, such as a dead sensor, an unresponsive valve, an unreadable
  calendar or a room heating without warming, appear under Settings → Repairs.
  They can also be sent to your phone or chat.
- **Every decision** has a plain-English status and a logbook entry.

## Install

1. In HACS, add this repository as a custom repository (type: Integration):
   `https://github.com/CHANGE-ME/fizz-hass`.
2. Install **Makerspace Heating** and restart Home Assistant.
3. Go to Settings → Devices & services → Add integration → **Makerspace Heating**.
   Choose the boiler's Shelly switch and the shared events calendar.
4. On the integration page choose **Add room** for each room. Pick its valve
   switch, temperature sensor, optional button, temperatures and a **tag**.
5. Open **Configure** on the integration to change shared settings: boost limits,
   boiler timings, alert targets and the control mode.

## Booking heat with the calendar

Put the room's tag anywhere in an event's title or description: `#workshop`,
`#main`. One event can carry several tags. All-day events are ignored. Typos show
up in the **Upcoming heating** sensor's `unknown_tags` attribute.

## Rolling out safely

New installs start in **Shadow** mode. Shadow decides everything and shows
statuses, stats and alerts, but switches nothing.

1. List every automation, schedule helper, timer and generic thermostat currently
   used for heating.
2. Recreate each schedule as calendar events with room tags. Use recurring events
   for weekly sessions.
3. Leave Makerspace Heating in Shadow for a few days. Compare each room's
   **Status** and the **Boiler status** with what the heating actually did.
4. Disable the old heating automations and schedule helpers, and remove the
   generic thermostats.
5. Configure → Control mode → **Active**.
6. Watch the boiler status and the logbook for the first day.

**Paused** stops all switching for maintenance or testing.

Shadow and Paused never switch any device. Leaving Active (choosing Shadow or
Paused, or disabling or deleting the integration) turns the boiler off but leaves
the valves as they are.

## Dashboard

`dashboards/makerspace-heating.yaml` is a ready-made dashboard using only built-in
cards. Paste it into the raw configuration editor of a new dashboard, then copy
the Workshop section for each room.

## Defaults

| Setting | Default |
|---|---|
| Default / maximum boost, dropdown step | 30 min / 2 h / 30 min |
| Valve margin | 0.3 °C |
| Wait for valves (0 = off) | 3 min |
| Boiler minimum run / rest | 5 min / 5 min |
| Keep valves open after stop | 3 min |
| Fault delay | 5 min |
| Heating but not warming | 60 min with < 0.5 °C rise |
| Calendar failure threshold | 30 min |
| Boost / event / frost target | 19 / 20 / 7 °C |
| Pre-heat | 30 min |

## To check on site

- Is there an automatic bypass valve? If so, *wait for valves* and *keep open*
  can be shortened or set to 0.
- How long do the valve actuators take to open? This sets *wait for valves*.
- Are the button inputs in button mode (event entities) or switch mode (binary
  sensors)? Both work.
- How often do the room temperature sensors report? This affects how quickly the
  valve margin reacts.

## Development

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
.venv/bin/pytest
```

The decision rules live in `custom_components/makerspace_heating/engine/` and
never import Home Assistant.

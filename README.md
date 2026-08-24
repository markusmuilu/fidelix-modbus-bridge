# Fidelix FX-2020 to Home Assistant

A redacted snapshot of a working home automation system: a **Modbus RTU bridge**
between a Fidelix FX-2020 building automation controller and **Home Assistant**,
plus the control layer above it.

The real system runs unattended in an occupied house. It reads 32 temperature
sensors off three analogue cards, drives 24 heating circuits plus auxiliary
outputs across five digital output cards, holds each zone at a setpoint while
leaning on cheap electricity, and watches itself for the kind of failure that
produces no error at all.

This repository is that code with the building removed. It is **not a library and
not a deployable package** - it is what was actually built, published so the
engineering can be read.

## What was redacted, and what was not

Removed: room names, Fidelix point tags, the serial device path, the site
coordinates, network addresses, the electricity tariff rates, and every credential.
Zones are `zone_01`..`zone_24`, shared-card points are neutral, and the two shared
card numbers were changed so the mapping matches no real cabinet.

Not removed: the logic, the structure, and the comments. Those are as written.
The comments are most of the value here - they record which assumptions are still
unverified, what was tried and reverted, and why.

Also not here: the input-card point list (a door-contact and motion-detector map
is a floor plan), the dashboards, the room map, and the solar and plant
integrations.

## The write-ups

The code is the smaller half of this repository.

- **[Two masters on one bus](docs/01-two-masters-on-one-bus.md)**. A replaced
  system that was never torn down, competing invisibly with its successor and
  wedging reads past their timeout for two days. Then the same defect again, from
  inside, shipped by the app whose docstring existed to prevent it.
- **[A running system is not a verified system](docs/02-a-running-system-is-not-a-verified-system.md)**.
  Five faults found in one afternoon in something that had been "working fine" for
  months, all silent, all found by a person reading source rather than by the
  system. What was built in response, and why it monitors ages rather than values.
- **[When software can open a door](docs/03-when-software-can-open-a-door.md)**.
  A design question about an output with an irreversible consequence. The answer
  is an interlock rather than a mask; the part worth reading is that the first
  attempt *was* a mask, and why refusing a capability that had already been asked
  for and cleared is a failure mode of its own rather than a safe default.
- **[A scheduler is not a thermostat](docs/04-a-scheduler-is-not-a-thermostat.md)**.
  The price optimiser this replaced picked the N cheapest slots of the day, ran
  fully open-loop on temperature, and could hold a room at 15 C through a cold snap
  while reporting that it was working as configured. Why closing the loop removes
  most of the need for a thermal model.
- **[Fidelix over Modbus RTU: field notes](docs/protocol-notes.md)**. Register
  layouts, the resistive-input formula, the 16-point input cards, and how to
  establish read-back fidelity before you write anything. Fidelix has essentially
  no open-source Home Assistant support, so none of this is documented elsewhere.

## The code

```
apps/
  modbus_bridge.py           the bridge: analogue polling, output writes,
                             shared-register read-modify-write, the interlock,
                             adopt-on-startup
  heating_rooms.py           the zone map, in one place
  heating_mode.py            which controller owns which output
  heating_from_maps.py       the plain thermostat
  heating_smart.py           closed-loop, price-biased thermostat
  heating_price_optimizer.py the open-loop version it replaced, kept unregistered
  system_health.py           the watchdog, after five silent faults
tests/                       offline harnesses, no hardware required
docs/                        the write-ups
```

Four things in `modbus_bridge.py` are worth reading on their own:

**Out-of-range is a fault, not a reading.** `resistance_to_temperature()` returns
`None` outside the table rather than clamping. A cut wire measures as near-infinite
resistance; clamped to the cold end of the table, a thermostat sees a room far
below setpoint and heats it forever. Both failures render on a dashboard as an
ordinary number.

**One master on the bus.** `bus()` is a context manager over a module-level lock,
acquired with a timeout, yielding `None` rather than blocking. A skipped read is a
gap in a graph; a blocked callback is a pinned thread.

**Shared-register read-modify-write, with an interlock.** `OUTPUT_INPUTS` cards are
composed and written whole. `SHARED_OUTPUT_INPUTS` cards are read first and only
the owned bits changed, because the rest of the register belongs to something else.
Guarded bits are gated by a manual switch, fail closed on anything ambiguous, and a
refused toggle is reverted rather than silently swallowed.

**Adopt on startup.** `_adopt_shared_outputs()` reads the hardware into Home
Assistant instead of asserting Home Assistant onto the hardware, so a restart -
including the hot reload a `git pull` triggers - is never an actuation.

## Running the tests

```
for f in tests/*.py; do python3 "$f"; done
```

No pytest, no AppDaemon, no pymodbus, no serial port: each harness stubs the
platform and imports the real app, so the bit arithmetic and the interlock can be
exercised against a fake register in about a second. Five suites, all passing.

## Licence

MIT. See [LICENSE](LICENSE).

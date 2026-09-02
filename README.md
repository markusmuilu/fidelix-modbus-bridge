# Fidelix FX-2020 to Home Assistant

The building's original control computer, a Windows CE machine, had failed. This is its
replacement, built from scratch: a Raspberry Pi running Home Assistant, with the whole
control layer written as AppDaemon apps in Python, driving the building's existing Fidelix
FX-2020 controller over Modbus RTU.

It controls 24 heating zones, each with its own temperature sensor and setpoint, and shifts
the heating into the cheaper hours of the Nord Pool spot price. Any zone can be switched to
manual, or back to a plain thermostat, at any time. It runs unattended in an occupied house.

A zone is one heating circuit with its own temperature sensor and setpoint. Zones do not
map one-to-one onto rooms.

Talking to the FX-2020 means Modbus RTU on a serial line: raw registers, resistive sensor
curves read through an NTC table, and output cards shared with equipment that must not be
disturbed. Fidelix has essentially no open-source Home Assistant support, so the protocol
work is written up in `docs/`.

This repository is that code with the building removed. It is **not a library and not a
deployable package** - it is what was actually built, published so the engineering can be
read.

## Four modes, chosen per zone

Each zone picks which controller drives its heating. The choice is the single source of
truth, so nothing overrides anything and a switch you set stays where you set it.

| Mode | What it does |
|---|---|
| **Price optimised** | Holds the zone at its setpoint but chooses when to spend the energy, biasing the heating toward the cheaper hours of the day's Nord Pool prices. Closed-loop on temperature, so a zone is never left cold to chase a cheap hour. |
| **Thermostat** | A plain setpoint thermostat that ignores price. The layer everything else sits on top of. |
| **Manual** | The zone's heating does what you set it to and stays there. |
| **Solar** | Runs on solar surplus, with a configurable allowance for how much grid import is acceptable. |

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
  system_health.py           the watchdog
tests/                       offline harnesses, no hardware required
docs/                        write-ups
```

## What was redacted, and what was not

Removed: room names, Fidelix point tags, the serial device path, the site coordinates,
network addresses, the electricity tariff rates, and every credential. Zones are
`zone_01`..`zone_24`, shared-card points are neutral, and the two shared card numbers were
changed so the mapping matches no real cabinet.

Not removed: the logic, the structure, and the comments. Those are as written.

Also not here: the input-card point list (a door-contact and motion-detector map is a floor
plan), the dashboards, the room map, and the solar and plant integrations.

## Write-ups

Longer notes on specific problems from building this, in `docs/`:

- [Two masters on one bus](docs/01-two-masters-on-one-bus.md)
- [A running system is not a verified system](docs/02-a-running-system-is-not-a-verified-system.md)
- [When software can open a door](docs/03-when-software-can-open-a-door.md)
- [A scheduler is not a thermostat](docs/04-a-scheduler-is-not-a-thermostat.md)
- [Fidelix over Modbus RTU: field notes](docs/protocol-notes.md) - register layouts, the
  resistive-input formula and the 16-point input cards.

## Running the tests

```
for f in tests/*.py; do python3 "$f"; done
```

No pytest, no AppDaemon, no pymodbus, no serial port: each harness stubs the platform and
imports the real app, so the bit arithmetic and the interlock can be exercised against a
fake register in about a second. Five suites, all passing.

## Licence

MIT. See [LICENSE](LICENSE).

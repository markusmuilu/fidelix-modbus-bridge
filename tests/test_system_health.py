"""Offline harness for system_health.py.

The cases that matter are the two silent failures that actually happened: a
Modbus thread wedged for two days, and a price feed pointing at a dead entity.

Run: python tests/test_system_health.py
"""
import sys, types, pathlib
from datetime import datetime, timedelta, timezone

APP = pathlib.Path(__file__).resolve().parent.parent / "apps"

hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
class Hass:  # noqa
    pass
hassapi.Hass = Hass
for name, mod in [
    ("appdaemon", types.ModuleType("appdaemon")),
    ("appdaemon.plugins", types.ModuleType("appdaemon.plugins")),
    ("appdaemon.plugins.hass", types.ModuleType("appdaemon.plugins.hass")),
    ("appdaemon.plugins.hass.hassapi", hassapi),
]:
    sys.modules[name] = mod

sys.path.insert(0, str(APP))
import system_health as h  # noqa: E402

NOW = datetime(2026, 8, 1, 16, 0, 0)

MODBUS = "sensor.modbus_last_read"
PRICE = "sensor.spot_prices"
PV = "sensor.pv_production"
P1 = "sensor.grid_power"
VERIFY = "sensor.modbus_output_check"


class FakeApp(h.SystemHealth):
    def __init__(self, ages_minutes=None, missing=()):
        """ages_minutes: entity -> how long ago it last updated."""
        self._now = NOW
        self._notified = False
        self.published = None
        self.logs = []
        self.services = []
        self.temp_sensors = {}

        ages = {MODBUS: 0.5, PRICE: 10, PV: 1, P1: 1, VERIFY: 1}
        ages.update(ages_minutes or {})

        self.states = {}
        for entity, mins in ages.items():
            if entity in missing:
                continue
            self.states[entity] = {
                "state": "123",
                "last_updated": (NOW - timedelta(minutes=mins)).isoformat(),
            }

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def datetime(self):
        return self._now

    def _utc_now(self):
        # The fake clock is naive local time at UTC+3, as HA's stamps are
        # written in this test unless a case says otherwise.
        return self._now.replace(tzinfo=timezone(timedelta(hours=3))).astimezone(timezone.utc)

    def get_state(self, entity, attribute=None, **kw):
        if entity == "sun.sun" and attribute == "elevation":
            return getattr(self, "sun_elevation", 30.0)
        if entity == "sensor":
            return {k: v["state"] for k, v in self.temp_sensors.items()}
        if entity in self.temp_sensors:
            return self.temp_sensors[entity]["state"]
        return self.states.get(entity)

    def set_state(self, entity, state=None, attributes=None):
        self.published = {"state": state, "attributes": attributes or {}}

    def call_service(self, service, **kw):
        self.services.append((service, kw))


failures = []

def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


print("\n1. Everything fresh -> ok, no notification")
app = FakeApp()
app.check({})
check("state", app.published["state"], h.OK)
check("no problems", app.published["attributes"]["problems"], [])
check("no notification", app.services, [])

print("\n2. The two-day wedged Modbus thread is caught")
# The real incident: stuck for days, found by chance.
app = FakeApp({MODBUS: 60 * 24 * 2})
app.check({})
check("state", app.published["state"], h.FAULT)
check("one problem", len(app.published["attributes"]["problems"]), 1)
check("names the bus", "Modbus" in app.published["attributes"]["problems"][0], True)
check("notified", app.services[0][0], "persistent_notification/create")

print("\n3. A dead price feed is caught")
app = FakeApp({PRICE: 60 * 12})
app.check({})
check("state", app.published["state"], h.FAULT)
check("names the feed", "Spot prices" in app.published["attributes"]["problems"][0], True)

print("\n4. A missing required entity is a fault, not silence")
# This is what the dead Nord Pool entity actually looked like: gone entirely.
app = FakeApp(missing=[PRICE])
app.check({})
check("state", app.published["state"], h.FAULT)
check("reported", "no data" in app.published["attributes"]["problems"][0], True)

print("\n5. Missing optional sources are fine")
# Not every install has solar. Absence must not cry wolf every minute.
app = FakeApp(missing=[PV, P1])
app.check({})
check("state", app.published["state"], h.OK)
check("marked unused", app.published["attributes"]["tarkistukset"]["PV production"],
      "not in use")

print("\n6. unavailable is treated as no data")
app = FakeApp()
app.states[MODBUS]["state"] = "unavailable"
app.check({})
check("state", app.published["state"], h.FAULT)

print("\n7. One notification per episode, not one per minute")
app = FakeApp({MODBUS: 999})
app.check({})
app._now += timedelta(minutes=1)
app.check({})
app._now += timedelta(minutes=1)
app.check({})
creates = [s for s, _ in app.services if s.endswith("create")]
check("notified once", len(creates), 1)

print("\n8. Recovery dismisses the notification")
app = FakeApp({MODBUS: 999})
app.check({})
app.states[MODBUS]["last_updated"] = (NOW - timedelta(seconds=20)).isoformat()
app._now += timedelta(minutes=1)
app.check({})
check("state", app.published["state"], h.OK)
check("dismissed", app.services[-1][0], "persistent_notification/dismiss")
check("can notify again later", app._notified, False)

print("\n9. Several problems at once are all listed")
app = FakeApp({MODBUS: 999, PRICE: 999})
app.check({})
check("two problems", len(app.published["attributes"]["problems"]), 2)

print("\n10. Timezone-aware stamps don't crash the comparison")
# HA's stamps carry a timezone; AppDaemon's datetime() is naive.
app = FakeApp()
app.states[MODBUS]["last_updated"] = (
    (NOW - timedelta(minutes=1)).isoformat() + "+03:00"
)
app.check({})
check("state", app.published["state"], h.OK)

print("\n10b. A UTC stamp is not aged by AppDaemon's UTC offset")
# The real failure: HA writes last_updated in UTC, AppDaemon's naive clock ran
# two hours ahead of it, and stripping the offset made a 1-minute-old heartbeat
# read as 121 minutes old - permanent vika.
app = FakeApp()
app.states[MODBUS]["last_updated"] = (
    (app._utc_now() - timedelta(minutes=1)).isoformat()
)
app.check({})
check("state", app.published["state"], h.OK)

print("\n11. A garbage timestamp is treated as no data, not a crash")
app = FakeApp()
app.states[MODBUS]["last_updated"] = "not a timestamp"
app.check({})
check("state", app.published["state"], h.FAULT)

print("\n12. A dead temperature sensor is reported while the bus is alive")
# Since the NTC fix, a cut wire reports unknown instead of clamping to -50 C.
# Unknown is only an improvement if somebody hears about it - one dead sensor
# among 32 is easy to miss on a dashboard.
app = FakeApp()
app.temp_sensors = {
    "sensor.zone_01_temperature": {"state": "21.4"},
    "sensor.zone_02_temperature": {"state": "unknown"},
}
app.check({})
check("state", app.published["state"], h.FAULT)
check("names the sensor", "zone_02" in app.published["attributes"]["problems"][0], True)

print("\n13. Dead sensors are NOT reported when the whole bus is down")
# Otherwise one serial fault produces thirty problems instead of one.
app = FakeApp({MODBUS: 999})
app.temp_sensors = {
    "sensor.zone_01_temperature": {"state": "unknown"},
    "sensor.zone_02_temperature": {"state": "unknown"},
}
app.check({})
check("exactly one problem", len(app.published["attributes"]["problems"]), 1)
check("and it is the bus", "Modbus" in app.published["attributes"]["problems"][0], True)

print("\n14. Healthy sensors report nothing")
app = FakeApp()
app.temp_sensors = {"sensor.zone_01_temperature": {"state": "21.4"}}
app.check({})
check("state", app.published["state"], h.OK)

print("\n15. An optional source that exists but is unavailable is a fault, and the grid meter self-heals")
app = FakeApp()
app.services = []
app._last_heal = {}
app.states[P1] = {"state": "unavailable",
                  "last_changed": (NOW - timedelta(minutes=40)).isoformat()}
app.check({})
check("fault", app.published["state"], h.FAULT)
check("named", any("Grid meter" in p for p in app.published["attributes"]["problems"]), True)
reloads = lambda: [s for s, _ in app.services if s == "homeassistant/reload_config_entry"]
check("reload called", len(reloads()), 1)
app.check({})
check("reload throttled", len(reloads()), 1)
app._now = NOW + timedelta(hours=1, minutes=1)
app.states[P1]["last_changed"] = (NOW - timedelta(minutes=40)).isoformat()
app.check({})
check("retried after an hour", len(reloads()), 2)

print("\n16. A short blip is not yet a fault")
app = FakeApp()
app._last_heal = {}
app.states[P1] = {"state": "unavailable",
                  "last_changed": (NOW - timedelta(minutes=5)).isoformat()}
app.check({})
check("ok", app.published["state"], h.OK)
check("no reload", [s for s, _ in app.services if "reload" in s], [])

print("\n17. An optional source that does not exist at all is still fine")
app = FakeApp(missing=[P1])
app._last_heal = {}
app.check({})
check("ok", app.published["state"], h.OK)

print("\n18. The inverter asleep at night is not a fault")
app = FakeApp()
app._last_heal = {}
app.sun_elevation = -12.0
app.states[PV] = {"state": "unavailable",
                  "last_changed": (NOW - timedelta(hours=6)).isoformat()}
app.check({})
check("ok", app.published["state"], h.OK)
check("no reload", [s for s, _ in app.services if "reload" in s], [])

print("\n19. The inverter unavailable in daylight is a fault, and gets reloaded")
app = FakeApp()
app._last_heal = {}
app.sun_elevation = 25.0
app.states[PV] = {"state": "unavailable",
                  "last_changed": (NOW - timedelta(hours=2)).isoformat()}
app.check({})
check("fault", app.published["state"], h.FAULT)
check("reloaded", len([s for s, _ in app.services if "reload" in s]), 1)

print("\n20. The inverter reporting a constant 0 W at dawn is not stale")
app = FakeApp({PV: 40})
app._last_heal = {}
app.sun_elevation = 1.9
app.check({})
check("ok", app.published["state"], h.OK)
app = FakeApp({PV: 40})
app._last_heal = {}
app.sun_elevation = 20.0
app.check({})
check("but stale in daylight is a fault", app.published["state"], h.FAULT)

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
sys.exit(1 if failures else 0)

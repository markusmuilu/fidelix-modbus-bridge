"""Offline harness for the full-register output path (DO modules 10, 11, 12, 15).

The failure this exists for: a write skipped for a busy bus or
failed on the wire was never repeated, so one zone's floor (module 11 bit 4)
ran for three days with HA showing it off, then sat off for three with HA
showing it on. These cases pin down that a write which did not land is retried,
and that a register which drifted from HA is read back and rewritten.

Run: python tests/test_output_writes.py
"""
import sys, types, pathlib
from datetime import datetime

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
pmc = types.ModuleType("pymodbus.client")
pmc.ModbusSerialClient = object
sys.modules["pymodbus"] = types.ModuleType("pymodbus")
sys.modules["pymodbus.client"] = pmc

sys.path.insert(0, str(APP))
import modbus_bridge as m  # noqa: E402

M11 = m.OUTPUT_INPUTS[11]
ZONE_16 = M11[4]
ZONE_15 = M11[2]


class Result:
    def __init__(self, error=False):
        self._e = error

    def isError(self):
        return self._e


class FakeApp(m.ModbusAllInOne):
    def __init__(self, states=None, fail_writes=0, busy=False, write_raises=False):
        self.bits = {mod: [False] * 8 for mod in m.OUTPUT_INPUTS}
        self.states = {e: "off" for ents in m.OUTPUT_INPUTS.values() for e in ents}
        self.states.update(states or {})
        self.hardware = {mod: 0 for mod in m.OUTPUT_INPUTS}
        self.fail_writes = fail_writes
        self.write_raises = write_raises
        self.busy = busy
        self.writes = []
        self.logs = []
        self.timers = []
        self.published = {}
        self._retry_pending = set()
        self._corrections = 0
        self._last_correction = None
        self._adopting = set()

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def datetime(self):
        return datetime(2026, 10, 2, 14, 0, 0)

    def get_state(self, entity, **kw):
        return self.states.get(entity)

    def set_state(self, entity, state=None, attributes=None):
        self.published[entity] = {"state": state, "attributes": attributes or {}}

    def run_in(self, cb, delay, **kwargs):
        self.timers.append((cb, delay, kwargs))

    def fire_timers(self):
        timers, self.timers = self.timers, []
        for cb, _d, kw in timers:
            cb(kw)

    def _read_register(self, mod):
        return self.hardware[mod]

    def bus(self, what="bus"):
        app = self

        class Ctx:
            def __enter__(self_):
                if app.busy:
                    return None

                class C:
                    def write_register(self, address, value, device_id):
                        if app.write_raises:
                            raise IOError("no response")
                        if app.fail_writes:
                            app.fail_writes -= 1
                            return Result(error=True)
                        app.writes.append((device_id, value))
                        app.hardware[device_id] = value
                        return Result()
                return C()

            def __exit__(self_, *a):
                return False
        return Ctx()


failures = []

def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


print("\n1. A write that lands reports success and schedules nothing")
app = FakeApp()
app.bits[11][4] = True
check("landed", app._write_register(11), True)
check("hardware", app.hardware[11], 0b00010000)
check("no retry", app.timers, [])

print("\n2. An error response is retried, with the bits current at retry time")
app = FakeApp(fail_writes=1)
app.bits[11][4] = True
check("first attempt fails", app._write_register(11), False)
check("retry scheduled", len(app.timers), 1)
check("retry delay", app.timers[0][1], m.WRITE_RETRY_SECONDS)
app.bits[11][2] = True                       # another zone turned on meanwhile
app.fire_timers()
check("retry carries latest intent", app.hardware[11], 0b00010100)
check("nothing left pending", app._retry_pending, set())

print("\n3. A busy bus is retried, not dropped")
app = FakeApp(busy=True)
app.bits[11][4] = True
check("skipped", app._write_register(11), False)
check("warned", any(l == "WARNING" and "bus busy" in msg for l, msg in app.logs), True)
app.busy = False
app.fire_timers()
check("landed on retry", app.hardware[11], 0b00010000)

print("\n4. An exception on the wire is retried, not raised into AppDaemon")
app = FakeApp(write_raises=True)
app.bits[11][4] = True
check("no raise, reports failure", app._write_register(11), False)
check("retry scheduled", len(app.timers), 1)

print("\n5. Repeated failures never stack more than one retry per module")
app = FakeApp(busy=True)
app._write_register(11)
app._write_register(11)
app._write_register(11)
check("one timer", len(app.timers), 1)

print("\n6. Verify: a case from the field - HA says one zone on, hardware has it off")
app = FakeApp(states={ZONE_16: "on"})
app.hardware[11] = 0
app._verify_outputs({})
check("rewritten", app.hardware[11], 0b00010000)
check("drift logged", any("[DRIFT] module=11" in msg for _, msg in app.logs), True)
check("counted", app._corrections, 1)
check("published", app.published[m.VERIFY_SENSOR]["attributes"]["corrections_since_start"], 1)

print("\n7. Verify: a case from the field - HA says off, hardware still has it on")
app = FakeApp()
app.hardware[11] = 0b00010000
app._verify_outputs({})
check("cleared", app.hardware[11], 0)

print("\n8. Verify reads intent from HA, so a missed callback is corrected too")
app = FakeApp(states={ZONE_15: "on"})
app.bits[11][2] = False                      # the callback never arrived
app._verify_outputs({})
check("bits refreshed", app.bits[11][2], True)
check("hardware follows HA", app.hardware[11], 0b00000100)

print("\n9. Verify writes nothing when hardware already matches")
app = FakeApp(states={ZONE_15: "on"})
app.hardware[11] = 0b00000100
app._verify_outputs({})
check("no writes", app.writes, [])
check("no corrections", app._corrections, 0)
check("all four modules checked",
      app.published[m.VERIFY_SENSOR]["attributes"]["modules_checked"], len(m.OUTPUT_INPUTS))

print("\n10. An unknown HA state keeps the last known intent rather than forcing off")
app = FakeApp(states={ZONE_16: "unavailable"})
app.bits[11][4] = True
app.hardware[11] = 0b00010000
app._verify_outputs({})
check("still on", app.hardware[11], 0b00010000)

print("\n11. If no module can be read, the sensor is left stale for the watchdog")
app = FakeApp()
app._read_register = lambda mod: None
app._verify_outputs({})
check("not published", m.VERIFY_SENSOR in app.published, False)

print("\n12. Only the low byte is compared - DO-8 has eight relays")
app = FakeApp()
app.hardware[10] = 0xFF00
app._verify_outputs({})
check("no rewrite for high-byte noise", [w for w in app.writes if w[0] == 10], [])

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
sys.exit(1 if failures else 0)

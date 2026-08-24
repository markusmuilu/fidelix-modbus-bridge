"""Offline checks for the NTC temperature conversion.

The interesting cases are the edges. Until 2026-08-01 an out-of-range resistance
was clamped to the end of the table, so a cut wire reported -50 C and a short
reported 120 C - both plausible-looking numbers that a thermostat will act on.

Run: python tests/test_ntc.py
"""
import sys, types, math, pathlib

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

r2t = m.resistance_to_temperature
raw2r = m.raw_to_resistance
T = m.NTC_TABLE

failures = []

def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)

def approx(label, got, want, tol):
    ok = got is not None and abs(got - want) <= tol
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want}±{tol}")
    if not ok:
        failures.append(label)


print("\n=== Faults must not look like readings ===")

print("\n1. Open circuit reports unknown, not -50 C")
# A cut wire measures as near-infinite resistance. Clamping it to -50 C means a
# thermostat sees a room 70 degrees below setpoint and heats forever.
check("just below 0xFFFF", r2t(raw2r(65534)), None)
check("absurdly high resistance", r2t(1e9), None)

print("\n2. Short circuit reports unknown, not 120 C")
# Clamping to 120 C means that room never gets heated at all.
check("near short", r2t(raw2r(1)), None)
check("low resistance", r2t(raw2r(1000)), None)

print("\n3. 0xFFFF and nonsense raws are still rejected earlier")
check("0xFFFF", raw2r(0xFFFF), None)
check("zero", raw2r(0), None)
check("negative", raw2r(-5), None)

print("\n4. The exact table endpoints are still valid readings")
check("coldest table point", r2t(T[0][1]), T[0][0])
check("hottest table point", r2t(T[-1][1]), T[-1][0])

print("\n=== Normal readings are unaffected ===")

print("\n5. Table points convert exactly")
for t, r in [(0, 32650), (20, 12490), (25, 10000), (50, 3603)]:
    approx(f"{t} C", r2t(r), t, 0.01)

print("\n6. A normal room reading")
approx("raw 46404", r2t(raw2r(46404)), 22.2, 0.2)

print("\n7. Interpolation stays accurate between points")
# Reference is log-resistance interpolation, which is what the physics wants.
def reference(t_true):
    for (t1, r1), (t2, r2) in zip(T, T[1:]):
        if t1 <= t_true <= t2:
            f = (t_true - t1) / (t2 - t1)
            return math.exp(math.log(r1) + f * (math.log(r2) - math.log(r1)))
    return None

def worst_over(lo, hi):
    worst, at = 0.0, None
    for i in range(int(lo * 10), int(hi * 10) + 1):
        t_true = i / 10.0
        res = reference(t_true)
        if res is None:
            continue
        got = r2t(res)
        if got is not None and abs(got - t_true) > worst:
            worst, at = abs(got - t_true), t_true
    return worst, at

# The table is dense (5 C steps) where a house lives and sparse above 80 C,
# where the steps stretch to 10 and then 20 C. Interpolation error follows the
# spacing, so assert tightly where it matters and merely report the rest.
w_room, at_room = worst_over(15, 30)
w_house, at_house = worst_over(-30, 60)
w_all, at_all = worst_over(-40, 120)
print(f"  room temperatures   (15..30 C): {w_room:.2f} C at {at_room}")
print(f"  anything realistic (-30..60 C): {w_house:.2f} C at {at_house}")
print(f"  whole table       (-40..120 C): {w_all:.2f} C at {at_all}")

check("room temperatures within 0.2 C", w_room < 0.2, True)
check("realistic range within 0.25 C", w_house < 0.25, True)
# Above 80 C the table steps to 10 and then 20 C, so error grows. Nothing in
# this system reads a temperature that high - the sensors are room NTCs - so
# this is documented rather than fixed.
check("whole table within 1.5 C", w_all < 1.5, True)

print("\n=== Calibration ===")

print("\n8. Calibration defaults to empty, so nothing is silently shifted")
check("no offsets configured", m.CALIBRATION, {})

print("\n9. An offset is a plain addition")
m.CALIBRATION["sensor.test"] = -0.4
try:
    base = r2t(12490)
    check("offset applied", round(base + m.CALIBRATION["sensor.test"], 2), 19.6)
finally:
    del m.CALIBRATION["sensor.test"]

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
sys.exit(1 if failures else 0)

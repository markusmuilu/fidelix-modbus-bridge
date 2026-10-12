"""Offline harness for heating_smart.py.

The claim under test is "give it a temperature and it keeps the room there as
cheaply as it can". Two halves to that: it must actually track the temperature
(closed loop, floor never breached) and it must lean on price (aim high when
cheap, coast when dear).

Run: python tests/test_heating_smart.py
"""
import sys, types, pathlib, time
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
pmc = types.ModuleType("pymodbus.client")
pmc.ModbusSerialClient = object
sys.modules["pymodbus"] = types.ModuleType("pymodbus")
sys.modules["pymodbus.client"] = pmc
if not hasattr(time, "tzset"):
    time.tzset = lambda: None

sys.path.insert(0, str(APP))
import heating_smart as hs  # noqa: E402
import heating_mode as hm  # noqa: E402
from heating_rooms import LOOPS  # noqa: E402

ROOM = "zone_01"
CFG = LOOPS[ROOM]


class FakeApp(hs.HeatingSmart):
    def __init__(self, temp=21.0, target=21.0, flex=1.0, mode=hm.OPTIMIZER, ranks=None):
        self.states = {}
        for room, cfg in LOOPS.items():
            self.states[cfg["temp"]] = "21.0"
            self.states[cfg["setpoint"]] = "21.0"
            self.states[cfg["relay"]] = "off"
            self.states[hs.flex_of(room)] = "1.0"
            self.states[hm.select_of(room)] = hm.MANUAL
        self.states[CFG["temp"]] = str(temp)
        self.states[CFG["setpoint"]] = str(target)
        self.states[hs.flex_of(ROOM)] = str(flex)
        self.states[hm.select_of(ROOM)] = mode

        self._ranks = ranks if ranks is not None else {}
        self._forced_rank = None
        self._over = {p: 0 for p in hs.PHASE_SENSORS}
        self._solar_rooms = set()
        self._import_cycles = 0
        self._pv_wh = {}
        self._fc_kw = {}
        self._distrust = None
        self.logs = []
        self.services = []
        self.published = {}

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def get_state(self, entity, attribute=None, **kw):
        if attribute == "last_updated":
            return self.states.get(entity + "@updated")
        return self.states.get(entity)

    def set_grid(self, grid_w, pv_w=2000, age_s=10):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
        for e, v in ((hs.GRID_SENSOR, grid_w), (hs.PV_SENSOR, pv_w)):
            self.states[e] = None if v is None else str(v)
            self.states[e + "@updated"] = stamp

    def set_plan(self, use_kw_by_hours_ahead, pv_kw_by_hours_ahead=None, issued_h_ago=0):
        hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        key = lambda a: (hour + timedelta(hours=a)).isoformat()
        self.states[hs.SOLAR_PLAN] = {"attributes": {
            "issued_utc": (hour - timedelta(hours=issued_h_ago)).isoformat(),
            "use_surplus_kw_utc": {key(a): kw for a, kw in use_kw_by_hours_ahead.items()},
            "pv_kw_utc": {key(a): kw for a, kw in (pv_kw_by_hours_ahead or use_kw_by_hours_ahead).items()},
        }}

    def set_p1(self, l1, l2, l3, age_s=10):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
        for phase, w in zip((1, 2, 3), (l1, l2, l3)):
            self.states[hs.PHASE_SENSORS[phase]] = str(w)
            self.states[hs.PHASE_SENSORS[phase] + "@updated"] = stamp

    def optimise(self, room, temp, target=21.0, relay="off"):
        cfg = LOOPS[room]
        self.states[hm.select_of(room)] = hm.OPTIMIZER
        self.states[cfg["temp"]] = str(temp)
        self.states[cfg["setpoint"]] = str(target)
        self.states[cfg["relay"]] = relay

    def set_state(self, entity, state=None, attributes=None):
        self.published[entity] = {"state": state, "attributes": attributes or {}}

    def call_service(self, service, entity_id=None, **kw):
        self.services.append((service, entity_id))
        self.states[entity_id] = "on" if service.endswith("turn_on") else "off"

    def current_rank(self):
        return self._forced_rank


failures = []

def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)

def approx(label, got, want, tol=0.01):
    ok = got is not None and abs(got - want) <= tol
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want}±{tol}")
    if not ok:
        failures.append(label)


print("\n=== Where it aims, given the price ===")

app = FakeApp()
print("\n1. Cheapest slot -> aim at the top of the band")
approx("rank 0.0", app.effective_target(21.0, 1.0, 0.0), 22.0)

print("\n2. Dearest slot -> aim at the floor, never below it")
approx("rank 1.0", app.effective_target(21.0, 1.0, 1.0), 20.0)

print("\n3. Mid price -> aim at the target")
approx("rank 0.5", app.effective_target(21.0, 1.0, 0.5), 21.0)

print("\n4. The floor is never breached at any rank")
worst = min(app.effective_target(21.0, 1.0, r / 100) for r in range(101))
approx("lowest aim across all ranks", worst, 20.0)

print("\n5. No price data -> aim at the floor, not off")
# Failing safe here means failing cheap: warm enough, never speculative.
approx("rank None", app.effective_target(21.0, 1.0, None), 20.0)

print("\n6. Flex scales the band")
approx("flex 2.0, cheap", app.effective_target(21.0, 2.0, 0.0), 23.0)
approx("flex 2.0, dear", app.effective_target(21.0, 2.0, 1.0), 19.0)
approx("flex 0 - no arbitrage, plain setpoint",
       app.effective_target(21.0, 0.0, 0.0), 21.0)

print("\n=== Closed-loop behaviour ===")

print("\n7. Cold room on cheap power heats")
app = FakeApp(temp=19.0)
app._forced_rank = 0.0
app.control({})
check("relay on", app.states[CFG["relay"]], "on")

print("\n8. Warm room on cheap power still stops at the top of the band")
# 22.5 is above target+flex, so even at the cheapest price it must not heat.
app = FakeApp(temp=22.5)
app._forced_rank = 0.0
app.control({})
check("relay off", app.states[CFG["relay"]], "off")

print("\n9. Expensive power coasts down through the band")
app = FakeApp(temp=21.5)
app._forced_rank = 1.0
app.control({})
check("relay off", app.states[CFG["relay"]], "off")

print("\n10. THE FLOOR HOLDS: cold room heats even at the dearest price")
# This is what fixes the old open-loop flaw. However expensive power gets, a
# room below its floor gets heat.
app = FakeApp(temp=19.0)
app._forced_rank = 1.0
app.control({})
check("relay on", app.states[CFG["relay"]], "on")

print("\n11. Solar gain needs no model - the loop just stops asking")
# A sunny room warms on its own; the controller sees the temperature and backs
# off. No irradiance term anywhere.
app = FakeApp(temp=22.8)
app._forced_rank = 0.2
app.control({})
check("relay off", app.states[CFG["relay"]], "off")

print("\n12. Deadband stops chattering")
app = FakeApp(temp=21.0)
app._forced_rank = 0.5          # aim exactly 21.0
app.control({})
check("no service call", app.services, [])

print("\n=== Scope and safety ===")

print("\n13. Only acts on rooms in PriceOptimised")
for mode in (hm.MANUAL, hm.THERMOSTAT, hm.SOLAR):
    app = FakeApp(temp=15.0, mode=mode)
    app._forced_rank = 0.0
    app.control({})
    check(f"silent in {mode}", app.services, [])

print("\n14. A missing temperature leaves the relay alone")
# The old code's failure mode was acting on data it did not have.
app = FakeApp(temp=19.0)
app.states[CFG["temp"]] = "unavailable"
app._forced_rank = 0.0
app.control({})
check("no service call", app.services, [])

print("\n15. It explains itself in Finnish")
app = FakeApp(temp=19.0)
app._forced_rank = 0.1
app.control({})
s = app.published[f"sensor.{ROOM}_target"]
check("cheap wording", "cheap" in s["attributes"]["reason"], True)
approx("published aim", float(s["state"]), 21.8)
app = FakeApp(temp=19.0)
app._forced_rank = 0.9
app.control({})
check("expensive wording",
      "expensive" in app.published[f"sensor.{ROOM}_target"]["attributes"]["reason"], True)

print("\n=== Price ranking ===")

print("\n16. Ranks run 0 to 1 over the horizon, cheapest first")
app = FakeApp()
base = hs.HeatingSmart.slot_key(datetime.now())
times, prices = [], []
for i in range(8):
    times.append((base + timedelta(minutes=15 * i)).isoformat())
    prices.append(0.10 - 0.01 * i)          # steadily cheaper
app.states[hs.PRICE_SENSOR] = {"attributes": {"times": times, "prices": prices}}
app.get_state = lambda e, attribute=None, **k: (
    app.states.get(e) if attribute is None else app.states.get(e))
app.recalculate(None)
ranks = sorted(app._ranks.values())
approx("min rank", ranks[0], 0.0)
approx("max rank", ranks[-1], 1.0)
check("one rank per slot", len(app._ranks), 8)
# The last slot is cheapest, so it should rank 0.
last_slot = hs.HeatingSmart.slot_key(base + timedelta(minutes=15 * 7))
approx("cheapest slot ranks 0", app._ranks[last_slot], 0.0)

print("\n17. Missing price data holds the previous ranks rather than clearing them")
app = FakeApp(ranks={base: 0.5})
app.states[hs.PRICE_SENSOR] = None
app.recalculate(None)
check("ranks kept", app._ranks, {base: 0.5})
check("warned", any(lvl == "WARNING" for lvl, _ in app.logs), True)

print("\n=== The target sensor exists in every mode ===")
# Regression: it used to be published only for rooms the optimiser drove. With
# 23 of 24 rooms on Manual that left 23 dashboard cards reading
# "Entiteettia ei loytynyt", which looks like a broken system rather than an
# idle one. Found from a screenshot, not from a test.

print("\n18. Every room publishes a tavoite sensor, whatever its mode")
app = FakeApp(mode=hm.MANUAL)
app._forced_rank = 0.5
app.control(None)
missing = [r for r in LOOPS if f"sensor.{r}_target" not in app.published]
check("rooms with no tavoite sensor", missing, [])

print("\n19. A room nobody is optimising reports unknown, not a made-up number")
entry = app.published[f"sensor.{ROOM}_target"]
check("state", entry["state"], "unknown")
check("reason names the owning mode", hm.MANUAL in entry["attributes"]["reason"], True)
check("band still readable", entry["attributes"]["floor"], 20.0)

print("\n20. Publishing in manual mode does not touch a single relay")
check("services called", app.services, [])

print("\n21. An optimised room still gets a real number")
app = FakeApp(temp=20.0, target=21.0, flex=1.0, mode=hm.OPTIMIZER)
app._forced_rank = 0.0
app.control(None)
approx("aim", app.published[f"sensor.{ROOM}_target"]["state"], 22.0)

print("\n22. An unreadable temperature reports unknown and leaves the relay alone")
app = FakeApp(mode=hm.OPTIMIZER)
app.states[CFG["temp"]] = None
app._forced_rank = 0.0
app.control(None)
check("state", app.published[f"sensor.{ROOM}_target"]["state"], "unknown")
check("relay untouched", [s for s in app.services if s[1] == CFG["relay"]], [])

print("\n=== Per-phase cap ===")
# L1 zones: zone_06 2138 W measured (2352 booked), zone_16 1468 estimate
# (1835 booked), zone_24 1015 measured (1117 booked).
ON = lambda app, r: app.states[LOOPS[r]["relay"]] == "on"

print("\n23. Room fits on its phase -> switches on")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(1000, 500, 500)
app.optimise("zone_06", 19.0)
app.control(None)
check("zone_06 on", ON(app, "zone_06"), True)

print("\n24. Phase full -> waits, and says why")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(3000, 500, 500)                      # 3000 + 2352 > 5000
app.optimise("zone_06", 19.0)
app.control(None)
check("held off", ON(app, "zone_06"), False)
check("reason names L1",
      "L1" in app.published["sensor.zone_06_target"]["attributes"]["reason"], True)

print("\n25. Coldest room first; the next one that fits still goes")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(1500, 500, 500)                      # 3500 W free on L1
app.optimise("zone_16", 17.0)                 # deficit 4.0, books 1835
app.optimise("zone_06", 19.5)            # deficit 1.5, books 2352
app.optimise("zone_24", 20.0)           # deficit 1.0, books 1117
app.control(None)
check("zone_16 on", ON(app, "zone_16"), True)
check("zone_06 waits", ON(app, "zone_06"), False)
check("zone_24 fits after it", ON(app, "zone_24"), True)

print("\n26. Other phases are not affected by a full L1")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(4900, 500, 500)
app.optimise("zone_06", 19.0)
app.optimise("zone_07", 19.0)           # L2
app.control(None)
check("L1 room waits", ON(app, "zone_06"), False)
check("L2 room on", ON(app, "zone_07"), True)

print("\n27. meter missing -> one start per cycle")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
for r in ("zone_06", "zone_07", "zone_05"):
    app.optimise(r, 18.0)
app.control(None)
check("one room started", sum(ON(app, r) for r in ("zone_06", "zone_07", "zone_05")), 1)

print("\n28. Stale meter counts as missing")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(0, 0, 0, age_s=600)
for r in ("zone_06", "zone_07"):
    app.optimise(r, 18.0)
app.control(None)
check("one room started", sum(ON(app, r) for r in ("zone_06", "zone_07")), 1)

print("\n29. Unknown element needs room on every phase")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(500, 4000, 500)
app.optimise("zone_22", 18.0)
app.control(None)
check("held while L2 is busy", ON(app, "zone_22"), False)
app.set_p1(500, 500, 500)
app.control(None)
check("on when all phases have room", ON(app, "zone_22"), True)

print("\n30. Over the fuse figure two cycles -> shed the room nearest its aim")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.optimise("zone_06", 20.9, relay="on")    # inside deadband, keeps heating
app.optimise("zone_24", 20.8, relay="on")
app.optimise("zone_02", 20.9, relay="on")      # L3, must be left alone
app.set_p1(6200, 500, 500)
app.control(None)
check("nothing shed after one cycle", ON(app, "zone_06") and ON(app, "zone_24"), True)
app.control(None)
check("warmest L1 room shed", ON(app, "zone_06"), False)
check("colder L1 room kept", ON(app, "zone_24"), True)
check("L3 room untouched", ON(app, "zone_02"), True)

print("\n31. Manual rooms are never shed")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.states[LOOPS["zone_06"]["relay"]] = "on"
app.set_p1(6200, 500, 500)
app.control(None); app.control(None)
check("manual relay left on", ON(app, "zone_06"), True)

print("\n32. Every room with a known element has a phase 1-3")
bad = [r for r in LOOPS if hs.element_of(r) and hs.element_of(r)["phase"] not in (1, 2, 3)]
check("bad phases", bad, [])

print("\n=== Solar ===")
# zone_02: 1050 W label, L3. Target 21, flex 1 -> top 22.

print("\n33. Exporting enough -> a room inside its band heats toward the top")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5          # aim 21.0
app.set_p1(500, 500, 0); app.set_grid(-1500)
app.optimise("zone_02", 21.2)
app.control(None)
check("on for solar", ON(app, "zone_02"), True)
check("marked solar room", "zone_02" in app._solar_rooms, True)
check("status says heating on sun",
      app.published[hs.SOLAR_STATUS]["state"], "heating on solar surplus")

print("\n34. Export too small for the element -> stays off")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(500, 500, 0); app.set_grid(-600)                    # 600 + 200 < 1050
app.optimise("zone_02", 21.2)
app.control(None)
check("off", ON(app, "zone_02"), False)

print("\n35. Export budget is shared: second room waits")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(500, 500, 0); app.set_grid(-1500)
app.optimise("zone_02", 21.0)                             # 1050 W
app.optimise("zone_05", 21.2)                             # 1200 W
app.control(None)
check("coldest-in-band on", ON(app, "zone_02"), True)
check("other waits", ON(app, "zone_05"), False)

print("\n36. Solar room keeps heating above its normal aim while exporting")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(500, 500, 1100); app.set_grid(-300)
app.optimise("zone_02", 21.6, relay="on")
app._solar_rooms.add("zone_02")
app.control(None)
check("still on", ON(app, "zone_02"), True)

print("\n37. Stops at the top of the band")
app.optimise("zone_02", 22.05, relay="on")
app.control(None)
check("off at top", ON(app, "zone_02"), False)

print("\n38. Cloud: importing two cycles -> solar room off; one cycle is not enough")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(500, 500, 1100)
app.optimise("zone_02", 21.4, relay="on")
app._solar_rooms.add("zone_02")
app.set_grid(900, pv_w=400)
app.control(None)
check("kept after one cycle", ON(app, "zone_02"), True)
app.control(None)
check("off after two", ON(app, "zone_02"), False)

print("\n39. Sun gone (PV under 300 W) -> no longer a solar room, back to normal aim")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(500, 500, 1100); app.set_grid(-100, pv_w=100)
app.optimise("zone_02", 21.6, relay="on")
app._solar_rooms.add("zone_02")
app.control(None)
check("off", ON(app, "zone_02"), False)

print("\n40. Grid meter missing -> no solar starts")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(500, 500, 0); app.set_grid(-1500, age_s=600)
app.optimise("zone_02", 21.2)
app.control(None)
check("off", ON(app, "zone_02"), False)

print("\n41. A room below its normal aim still heats on grid, sun or no sun")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.5
app.set_p1(500, 500, 0); app.set_grid(2000, pv_w=0)
app.optimise("zone_02", 20.0)
app.control(None)
check("on", ON(app, "zone_02"), True)
check("not a solar room", "zone_02" in app._solar_rooms, False)

print("\n42. Holdback: surplus coming -> cheap-hour aim capped at target")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.0          # cheap: aim would be 22
app.set_p1(500, 500, 0); app.set_grid(1000, pv_w=0)
app.set_plan({2: 1.0, 3: 1.5})
app.optimise("zone_02", 21.2)
app.control(None)
approx("aim", app.published["sensor.zone_02_target"]["state"], 21.0)
check("no grid preheat", ON(app, "zone_02"), False)
check("says why", "sun expected" in
      app.published["sensor.zone_02_target"]["attributes"]["reason"], True)

print("\n43. Holdback never touches the floor")
app.optimise("zone_02", 19.5)
app._forced_rank = 1.0                                         # aim 20.0
app.control(None)
check("heats below floor", ON(app, "zone_02"), True)

print("\n44. Too little surplus ahead -> no holdback")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.0
app.set_p1(500, 500, 0); app.set_grid(1000, pv_w=0)
app.set_plan({2: 0.5, 9: 3.0})                                 # 9 h is outside the window
app.optimise("zone_02", 21.2)
app.control(None)
check("preheats on cheap grid", ON(app, "zone_02"), True)

print("\n45. Stale forecast -> ignored")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.0
app.set_p1(500, 500, 0); app.set_grid(1000, pv_w=0)
app.set_plan({1: 3.0}, issued_h_ago=5)
app.optimise("zone_02", 21.2)
app.control(None)
check("preheats", ON(app, "zone_02"), True)

print("\n46. Garbage forecast -> ignored, no crash")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.0
app.set_p1(500, 500, 0); app.set_grid(1000, pv_w=0)
app.states[hs.SOLAR_PLAN] = {"attributes": {"issued_utc": "yesterday", "pv_kw_utc": 5}}
app.optimise("zone_02", 21.2)
app.control(None)
check("preheats", ON(app, "zone_02"), True)

print("\n47. Forecast far above production over completed hours -> distrusted, holdback off")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.0
app.set_p1(500, 500, 0); app.set_grid(1000, pv_w=100)
hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
for back in (1, 2):
    h = hour - timedelta(hours=back)
    app._fc_kw[h] = 1.5                                        # promised 3 kWh
    app._pv_wh[h] = [300.0, 60]                                # got 0.6
app.set_plan({1: 2.0, 2: 2.0})
app.optimise("zone_02", 21.2)
app.control(None)
check("distrusted", app._distrust is not None, True)
check("preheats anyway", ON(app, "zone_02"), True)
check("status shows it",
      app.published[hs.SOLAR_STATUS]["attributes"]["forecast_distrusted"] is not None, True)

print("\n48. Forecast close to production -> trusted")
app = FakeApp(mode=hm.MANUAL); app._forced_rank = 0.0
app.set_p1(500, 500, 0); app.set_grid(1000, pv_w=100)
for back in (1, 2):
    h = hour - timedelta(hours=back)
    app._fc_kw[h] = 1.5
    app._pv_wh[h] = [1300.0, 60]
app.set_plan({1: 2.0})
app.control(None)
check("trusted", app._distrust, None)

print("\n49. Distrust ends with the day")
app._distrust = (datetime.now().date() - timedelta(days=1), "old")
app.control(None)
check("cleared", app._distrust, None)

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
sys.exit(1 if failures else 0)

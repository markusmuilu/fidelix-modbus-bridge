"""Offline harness for heating_smart.py.

The claim under test is "give it a temperature and it keeps the room there as
cheaply as it can". Two halves to that: it must actually track the temperature
(closed loop, floor never breached) and it must lean on price (aim high when
cheap, coast when dear).

Run: python tests/test_heating_smart.py
"""
import sys, types, pathlib, time
from datetime import datetime, timedelta

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
        self.logs = []
        self.services = []
        self.published = {}

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def get_state(self, entity, attribute=None, **kw):
        return self.states.get(entity)

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
# idle one. Found 2026-08-02 from a screenshot, not from a test.

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

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
sys.exit(1 if failures else 0)

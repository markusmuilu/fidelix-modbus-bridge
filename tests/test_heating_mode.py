"""Offline harness for the mode layer (backlog item 6).

Covers heating_mode.py plus the gating it added to the thermostat and the price
optimiser. The case that matters most is the last one: the optimiser must no
longer switch the thermostat's boolean off behind the user's back.

Run: python tests/test_heating_mode.py
"""
import sys, types, pathlib, time

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

# heating_price_optimizer calls time.tzset() at import; POSIX-only, absent on
# Windows. The call is a real workaround for AppDaemon's Amsterdam default, so
# it stays in the app - the harness just has to survive it.
if not hasattr(time, "tzset"):
    time.tzset = lambda: None

sys.path.insert(0, str(APP))
import heating_mode as hm            # noqa: E402
import heating_from_maps as hfm      # noqa: E402
import heating_price_optimizer as hpo  # noqa: E402
from heating_rooms import LOOPS      # noqa: E402

ROOM = "zone_01"
CFG = LOOPS[ROOM]
SELECT = hm.select_of(ROOM)
THERMO_BOOL = hm.thermostat_boolean_of(ROOM)
OPT_BOOL = hm.optimizer_boolean_of(ROOM)


class Base:
    """Shared fake HA state machine."""

    def _init_states(self, extra=None):
        self.states = {}
        for room, cfg in LOOPS.items():
            self.states[hm.select_of(room)] = hm.MANUAL
            self.states[hm.thermostat_boolean_of(room)] = "off"
            self.states[hm.optimizer_boolean_of(room)] = "off"
            self.states[cfg["temp"]] = "20.0"
            self.states[cfg["setpoint"]] = "21.0"
            self.states[cfg["relay"]] = "off"
            self.states[f"input_number.{room}_heating_hours"] = "4"
        self.states.update(extra or {})
        self.logs = []
        self.services = []

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def get_state(self, entity, **kw):
        return self.states.get(entity)

    def call_service(self, service, entity_id=None, option=None, **kw):
        self.services.append((service, entity_id, option))
        if service == "input_select/select_option":
            self.states[entity_id] = option
        elif service.endswith("turn_on"):
            self.states[entity_id] = "on"
        elif service.endswith("turn_off"):
            self.states[entity_id] = "off"

    def datetime(self):
        import datetime as d
        return d.datetime(2026, 8, 1, 17, 0, 0)


class FakeMode(Base, hm.HeatingMode):
    def __init__(self, extra=None):
        self._init_states(extra)
        self._applying = set()


class FakeThermostat(Base, hfm.HeatingFromMaps):
    def __init__(self, extra=None):
        self._init_states(extra)


class FakeOptimizer(Base, hpo.HeatingPriceOptimizer):
    def __init__(self, extra=None):
        self._init_states(extra)
        self.allowed_slots = {r: set() for r in LOOPS}


failures = []

def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


print("\n=== Migration from the old booleans ===")

print("\n1. Thermostat boolean on -> Thermostat")
app = FakeMode({THERMO_BOOL: "on"})
app._migrate(None)
check("mode", app.states[SELECT], hm.THERMOSTAT)

print("\n2. Optimiser boolean on -> PriceOptimised")
app = FakeMode({OPT_BOOL: "on"})
app._migrate(None)
check("mode", app.states[SELECT], hm.OPTIMIZER)

print("\n3. Both on -> PriceOptimised, reproducing the old precedence exactly")
# The old code let the optimiser win. Migration must not silently change which
# control is driving a room on the day this deploys.
app = FakeMode({THERMO_BOOL: "on", OPT_BOOL: "on"})
app._migrate(None)
check("mode", app.states[SELECT], hm.OPTIMIZER)
check("thermostat boolean now mirrors off", app.states[THERMO_BOOL], "off")

print("\n4. Neither on -> Manual")
app = FakeMode()
app._migrate(None)
check("mode", app.states[SELECT], hm.MANUAL)

print("\n5. A deliberate choice is not overwritten by migration")
app = FakeMode({SELECT: hm.SOLAR, THERMO_BOOL: "on"})
app._migrate(None)
check("mode kept", app.states[SELECT], hm.SOLAR)

print("\n=== Mirroring, so old dashboard cards stay honest ===")

print("\n6. Selecting a mode drives the legacy booleans")
app = FakeMode()
app._select_changed(SELECT, None, hm.MANUAL, hm.OPTIMIZER, {"room": ROOM})
check("optimiser boolean on", app.states[OPT_BOOL], "on")
check("thermostat boolean off", app.states[THERMO_BOOL], "off")

print("\n7. Toggling an old boolean changes the mode")
app = FakeMode()
app.states[THERMO_BOOL] = "on"
app._legacy_changed(THERMO_BOOL, None, "off", "on", {"room": ROOM, "mode": hm.THERMOSTAT})
check("mode", app.states[SELECT], hm.THERMOSTAT)

print("\n8. Turning off the active mode's boolean falls back to manual")
app = FakeMode({SELECT: hm.THERMOSTAT, THERMO_BOOL: "on"})
app.states[THERMO_BOOL] = "off"
app._legacy_changed(THERMO_BOOL, None, "on", "off", {"room": ROOM, "mode": hm.THERMOSTAT})
check("mode", app.states[SELECT], hm.MANUAL)

print("\n9. Turning off a boolean that isn't the active mode does nothing")
# The mirror sets these off routinely; that must not knock the room to manual.
app = FakeMode({SELECT: hm.OPTIMIZER, OPT_BOOL: "on"})
app._legacy_changed(THERMO_BOOL, None, "on", "off", {"room": ROOM, "mode": hm.THERMOSTAT})
check("mode unchanged", app.states[SELECT], hm.OPTIMIZER)

print("\n10. The mirror's own writes don't loop back")
app = FakeMode()
app._applying.add(ROOM)
app._legacy_changed(THERMO_BOOL, None, "off", "on", {"room": ROOM, "mode": hm.THERMOSTAT})
check("no mode change", app.states[SELECT], hm.MANUAL)

print("\n11. mode_of falls back to the booleans when the select is missing")
# The first minutes after deploy, before the input_selects exist in HA.
app = FakeMode({OPT_BOOL: "on"})
del app.states[SELECT]
check("derived mode", hm.mode_of(app, ROOM), hm.OPTIMIZER)

print("\n=== The writers respect the mode ===")

print("\n12. Thermostat acts only in Thermostat")
app = FakeThermostat({SELECT: hm.THERMOSTAT, CFG["temp"]: "19.0"})
app.control({})
check("relay turned on", app.states[CFG["relay"]], "on")

print("\n13. Thermostat stays out of every other mode")
for mode in (hm.MANUAL, hm.OPTIMIZER, hm.SOLAR):
    app = FakeThermostat({SELECT: mode, CFG["temp"]: "19.0"})
    app.control({})
    check(f"silent in {mode}", app.states[CFG["relay"]], "off")

print("\n14. Optimiser acts only in PriceOptimised")
app = FakeOptimizer({SELECT: hm.OPTIMIZER})
app.enforce({})
touched = [e for _, e, _ in app.services if e == CFG["relay"]]
check("drove its relay", len(touched) > 0, True)

print("\n15. Optimiser stays out of every other mode")
for mode in (hm.MANUAL, hm.THERMOSTAT, hm.SOLAR):
    app = FakeOptimizer({SELECT: mode})
    app.enforce({})
    touched = [e for _, e, _ in app.services if e == CFG["relay"]]
    check(f"silent in {mode}", touched, [])

print("\n16. THE BUG: the optimiser no longer switches the thermostat off")
# Previously enforce() called turn_off on input_boolean.<room> whenever both were
# enabled - a switch the user had just set flipping back within 60 s, with nothing
# in the UI to explain it. That is what the owner was describing.
app = FakeOptimizer({SELECT: hm.OPTIMIZER, THERMO_BOOL: "on"})
app.enforce({})
disabled = [e for sv, e, _ in app.services if e == THERMO_BOOL and sv.endswith("turn_off")]
check("never touched the thermostat boolean", disabled, [])
check("thermostat boolean untouched", app.states[THERMO_BOOL], "on")

print("\n17. Modes are exclusive - two writers can never both act on one room")
for mode in hm.MODES:
    t = FakeThermostat({SELECT: mode, CFG["temp"]: "19.0"})
    t.control({})
    o = FakeOptimizer({SELECT: mode})
    o.enforce({})
    t_acted = t.states[CFG["relay"]] == "on"
    o_acted = any(e == CFG["relay"] for _, e, _ in o.services)
    check(f"{mode}: at most one writer", sum([t_acted, o_acted]) <= 1, True)

print("\n18. Every room has a select, and they all migrate")
app = FakeMode({hm.thermostat_boolean_of(r): "on" for r in LOOPS})
app._migrate(None)
wrong = [r for r in LOOPS if app.states[hm.select_of(r)] != hm.THERMOSTAT]
check("rooms not migrated", wrong, [])

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
sys.exit(1 if failures else 0)

"""Offline harness for the shared read-modify-write path (DO modules 20 and 21).

Imports the real modbus_bridge.py with appdaemon and pymodbus stubbed, then
drives _write_shared_register / _adopt_shared_outputs / _shared_input_changed
against a fake register, so the bit arithmetic and the security interlock can be
checked without a serial bus or a house.

20 scenarios. The interlock ones (4-11) are the load-bearing part: they are what
stands between a stray write and the front door locks on module 21.

Run: python tests/test_shared_write.py
"""
import sys, types, pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "apps"

# --- stub appdaemon + pymodbus ------------------------------------------------
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

PLANT = m.SHARED_OUTPUT_INPUTS[20]
MIXED = m.SHARED_OUTPUT_INPUTS[21]
INTERLOCK, GUARDED = m.INTERLOCKED_BITS[21]

# Module 21 bit meanings, for readable assertions.
GP1, LOAD1, LOAD2, GP2, GP3, GP4, LOAD3, LOAD4 = (MIXED[i] for i in range(8))


# --- fake house ---------------------------------------------------------------
class FakeApp(m.ModbusAllInOne):
    def __init__(self, register, states=None):
        self.register = register
        # Every entity defaults to off unless the test says otherwise.
        self.states = {e: "off" for e in list(PLANT.values()) + list(MIXED.values())}
        self.states[INTERLOCK] = "off"
        self.states.update(states or {})
        self.writes = []
        self.logs = []
        self.timers = []
        self._adopting = set()

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def get_state(self, entity):
        # Missing entity behaves like HA: no state at all.
        return self.states.get(entity)

    def turn_on(self, entity):
        self.states[entity] = "on"

    def turn_off(self, entity):
        self.states[entity] = "off"

    def run_in(self, callback, delay, **kwargs):
        self.timers.append((callback, delay, kwargs))

    def fire_timers(self):
        for cb, _delay, kwargs in list(self.timers):
            cb(kwargs)

    def _read_register(self, mod):
        return self.register

    def _client(self):
        app = self

        class C:
            def connect(self):
                return True

            def write_register(self, address, value, device_id):
                app.writes.append((device_id, value))
                app.register = value

            def close(self):
                pass

        return C()


failures = []

def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)

def has_level(app, level):
    return any(lvl == level for lvl, _ in app.logs)


# Module 21 with the burglar alarm, both locks and the siren energised.
SECURITY = (1 << 0) | (1 << 3) | (1 << 4) | (1 << 5)   # 0b00111001

print("\n=== Module 21: ordinary loads ===")

print("\n1. A pool toggle leaves the security bits alone")
app = FakeApp(SECURITY, {LOAD3: "on"})
app._write_shared_register(21)
check("register", f"0b{app.register:08b}", f"0b{SECURITY | (1 << 6):08b}")
check("security bits intact", app.register & SECURITY, SECURITY)

print("\n2. Turning a pool device off clears only its own bit")
app = FakeApp(SECURITY | (1 << 6) | (1 << 7), {LOAD4: "on"})
app._write_shared_register(21)
check("register", f"0b{app.register:08b}", f"0b{SECURITY | (1 << 7):08b}")

print("\n3. All four unguarded loads on, security bits low, stay low")
app = FakeApp(0, {LOAD1: "on", LOAD2: "on", LOAD3: "on", LOAD4: "on"})
app._write_shared_register(21)
check("register", f"0b{app.register:08b}", "0b11000110")
check("no security bit set", app.register & SECURITY, 0)

print("\n=== Module 21: the interlock ===")

print("\n4. Interlock closed: a door lock cannot be driven")
# HA thinks both locks should be energised. The interlock is off. Hardware wins.
app = FakeApp(0, {GP2: "on", GP3: "on"})
app._write_shared_register(21)
check("lock bits still low", app.register & ((1 << 3) | (1 << 4)), 0)
check("no write issued", app.writes, [])
check("warned", has_level(app, "WARNING"), True)

print("\n5. Interlock closed: an unguarded load still works alongside")
app = FakeApp(SECURITY, {GP2: "off", LOAD3: "on"})
app._write_shared_register(21)
check("pool bit set", bool(app.register & (1 << 6)), True)
check("lock 1 unchanged", bool(app.register & (1 << 3)), True)

print("\n6. Interlock open: the door lock is driven")
app = FakeApp(0, {INTERLOCK: "on", GP2: "on"})
app._write_shared_register(21)
check("lock 1 energised", bool(app.register & (1 << 3)), True)
check("one write issued", len(app.writes), 1)
check("logged as a security write", has_level(app, "WARNING"), True)

print("\n7. Interlock open: unlocking works too, and moves only that bit")
app = FakeApp(SECURITY, {INTERLOCK: "on", GP1: "on", GP3: "on", GP4: "on"})
app._write_shared_register(21)   # GP2 is off in HA, so bit 3 should clear
check("register", f"0b{app.register:08b}", f"0b{SECURITY & ~(1 << 3):08b}")
check("only one bit changed", bin(SECURITY ^ app.register).count("1"), 1)

print("\n8. A guarded toggle with the interlock closed reverts in the UI")
app = FakeApp(0)
app._shared_input_changed(GP2, None, "off", "on", {"module": 21, "bit": 3})
check("toggle reverted to off", app.states[GP2], "off")
check("no write issued", app.writes, [])
check("register untouched", app.register, 0)

print("\n9. An unguarded toggle with the interlock closed goes through")
app = FakeApp(0, {LOAD3: "on"})
app._shared_input_changed(LOAD3, None, "off", "on", {"module": 21, "bit": 6})
check("pool bit set", bool(app.register & (1 << 6)), True)

print("\n10. A missing or unavailable interlock entity reads as closed")
app = FakeApp(0, {GP2: "on"})
app.states[INTERLOCK] = "unavailable"
app._write_shared_register(21)
check("lock bit still low", app.register & (1 << 3), 0)
check("no write issued", app.writes, [])

print("\n11. The interlock is manual - opening it schedules nothing")
app = FakeApp(0, {INTERLOCK: "on"})
app._interlock_changed(INTERLOCK, None, "off", "on", {"module": 21})
check("no timer scheduled", app.timers, [])
check("interlock stays open", app.states[INTERLOCK], "on")
check("transition logged", has_level(app, "WARNING"), True)

print("\n=== Shared path: general behaviour ===")

print("\n11b. An owned entity with no usable state aborts the whole write")
# The real case: AppDaemon hot-reloads on `git pull`, so it can be running against
# a HA that does not know about the new input_booleans yet. Treating None as "off"
# would clear bits belonging to equipment that is running.
plant_all_on = 0b11111111
app = FakeApp(plant_all_on)
del app.states[PLANT[3]]                      # entity does not exist in HA
app.states[PLANT[5]] = "on"
app._write_shared_register(20)
check("no write issued", app.writes, [])
check("register untouched", f"0b{app.register:08b}", f"0b{plant_all_on:08b}")
check("logged at ERROR", has_level(app, "ERROR"), True)

print("\n11c. 'unavailable' is not 'off' either")
app = FakeApp(plant_all_on, {PLANT[2]: "unavailable"})
app._write_shared_register(20)
check("no write issued", app.writes, [])
check("register untouched", app.register, plant_all_on)

print("\n11d. A closed interlock ignores the state of bits it isn't applying")
# The four guarded bits are not being written, so their state is irrelevant -
# a missing one must not block an unrelated pool toggle.
app = FakeApp(0, {LOAD3: "on"})
del app.states[GP2]
app._write_shared_register(21)
check("pool bit set", bool(app.register & (1 << 6)), True)
check("write went through", len(app.writes), 1)

print("\n12. A failed read aborts the write entirely")
app = FakeApp(SECURITY, {LOAD3: "on"})
app._read_register = lambda mod: None
app._write_shared_register(21)
check("no write issued", app.writes, [])

print("\n13. A no-op toggle issues no bus write")
app = FakeApp(SECURITY)
app._write_shared_register(21)
check("no write issued", app.writes, [])

print("\n14. Startup adopts hardware state instead of asserting HA's")
app = FakeApp(SECURITY | (1 << 7))
app._adopt_shared_outputs({})
check("LOAD4 adopted as on", app.states[LOAD4], "on")
check("LOAD3 stays off", app.states[LOAD3], "off")
check("both guarded points adopted as on", (app.states[GP2], app.states[GP3]), ("on", "on"))
check("nothing written to the bus", app.writes, [])
check("register untouched", app.register, SECURITY | (1 << 7))

print("\n15. Adopting security bits does not need the interlock")
# Adoption is observation, not actuation - the locks must still be visible in HA.
check("interlock was closed throughout", app.states[INTERLOCK], "off")

print("\n16. Adopt guard suppresses the listener feedback loop")
app = FakeApp(SECURITY | (1 << 7))
app._adopting.add(21)
app._shared_input_changed(LOAD4, None, "off", "on", {"module": 21, "bit": 7})
check("no write during adoption", app.writes, [])

print("\n17. Failed read during adopt leaves HA state alone")
app = FakeApp(SECURITY, {LOAD3: "on"})
app._read_register = lambda mod: None
app._adopt_shared_outputs({})
check("LOAD3 untouched", app.states[LOAD3], "on")

print("\n=== Module 20: plant equipment ===")

print("\n18. A restart does not stop the plant")
# The failure this design exists to prevent. Ventilation, circulation pump and
# solenoid all running; HA has fresh input_booleans, all off. The full-register
# path would write 0x00 here on the next hot-reload.
plant = 0b10110011
app = FakeApp(plant)
app._adopt_shared_outputs({})
check("nothing written to the bus", app.writes, [])
check("register untouched", f"0b{app.register:08b}", f"0b{plant:08b}")
check("circulation pump adopted as on", app.states[PLANT[5]], "on")   # bit 5 high
check("VALVE adopted as on", app.states[PLANT[4]], "on")                # bit 4 high
check("AHU2 stage 1 adopted as off", app.states[PLANT[2]], "off")      # bit 2 low

print("\n19. Toggling the pump moves only the pump bit")
app = FakeApp(plant)
app._adopt_shared_outputs({})
app.states[PLANT[5]] = "off"
app._write_shared_register(20)
check("register", f"0b{app.register:08b}", f"0b{plant & ~(1 << 5):08b}")
check("only one bit changed", bin(plant ^ app.register).count("1"), 1)

print("\n20. Module 20 has no interlock, so nothing is held back")
app = FakeApp(0, {e: "on" for e in PLANT.values()})
app._write_shared_register(20)
check("all 8 bits set", f"0b{app.register:08b}", "0b11111111")
check("no interlock mask", app._interlocked_mask(13), 0)

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
sys.exit(1 if failures else 0)

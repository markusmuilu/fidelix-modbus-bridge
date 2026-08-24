import threading
import time
from contextlib import contextmanager
from datetime import timedelta
import appdaemon.plugins.hass.hassapi as hass
from pymodbus.client import ModbusSerialClient

# ONE CLIENT ON THE BUS AT A TIME. ALWAYS.
#
# pyserial takes an exclusive OS lock on the port, so a second client opening
# while the first is open does not merely collide - it fails outright with
# "Could not exclusively lock port". That is what happened on 2026-08-02, when
# modbus_di called this app's _client() expecting to share a client and got its
# own instead, because _client() is a factory. The bridge then could not write
# to any output module for as long as the DI reader was polling.
#
# This lock is module-level on purpose: AppDaemon runs every app in one Python
# process, so anything that imports this module shares it. Use the bus()
# context manager below rather than calling _client() directly.
BUS_LOCK = threading.Lock()

# How long to wait for the bus before giving up. A skipped read is a gap in a
# graph; a blocked callback is a pinned AppDaemon thread, which is the shape of
# the two-day hang on 2026-07-29.
BUS_WAIT_SECONDS = 5.0

# ======================================================
# MODBUS CONFIG
# ======================================================

PORT = "/dev/serial/by-id/REDACTED"

BAUDRATE = 9600
PARITY = "N"
STOPBITS = 1
BYTESIZE = 8
TIMEOUT = 1.0

# Module 4 is the outdoor sensor card. Added 2026-08-01: outdoor temperature is
# the single biggest input to any heating decision and was not in HA at all,
# which is why the optimiser can only pick "N cheapest slots" rather than "as
# many slots as it is actually cold enough to need".
TEMP_MODULES = [1, 2, 3, 4]
OUTPUT_MODULES = [10, 11, 12]

TEMP_BASE_ADDR = 0
OUTPUT_BASE_ADDR = 0

TEMP_SCALE = 1
INVALID_RAW = {0xFFFF}

POLL_SECONDS = 30
INTER_DEVICE_DELAY = 0.05

# Written after every poll attempt, successful or not. system_health.py watches
# its age - see the comment where it is published.
HEARTBEAT_SENSOR = "sensor.modbus_last_read"

# Temperature (C) : Resistance (ohm)
NTC_TABLE = [
    (-50, 670100),
    (-40, 336500),
    (-30, 177000),
    (-25, 130400),
    (-20, 97070),
    (-15, 72980),
    (-10, 55330),
    (-5, 42340),
    (0, 32650),
    (5, 25400),
    (10, 19900),
    (15, 15710),
    (20, 12490),
    (25, 10000),
    (30, 8057),
    (35, 6532),
    (40, 5327),
    (45, 4368),
    (50, 3603),
    (55, 2968),
    (60, 2488),
    (65, 2082),
    (70, 1752),
    (75, 1480),
    (80, 1258),
    (90, 917.7),
    (100, 680),
    (120, 389),
]


# ======================================================
# TEMPERATURE ENTITY NAMES
# ======================================================

TEMP_NAMES = {
    1: [
        "Zone 01 Temperature",
        "Zone 02 Temperature",
        "Zone 03 Temperature",
        "Zone 04 Temperature",
        "Zone 05 Temperature",
        "Zone 06 Temperature",
        "Zone 07 Temperature",
        "Zone 08 Temperature",
    ],
    2: [
        "Zone 09 Temperature",
        "Zone 10 Temperature",
        "Zone 11 Temperature",
        "Zone 12 Temperature",
        "Zone 13 Temperature",
        "Zone 14 Temperature",
        "Zone 15 Temperature",
        "Zone 16 Temperature",
    ],
    3: [
        "Zone 17 Temperature",
        "Zone 18 Temperature",
        "Zone 19 Temperature",
        "Zone 20 Temperature",
        "Zone 21 Temperature",
        "Zone 22 Temperature",
        "Zone 23 Temperature",
        "Zone 24 Temperature",
    ],
    # The AdConSys documentation names one point on this card OUTDOOR, the outdoor
    # sensor, but not which of the eight. So all eight are published under
    # neutral names and the data identifies itself: an unused NTC input reads
    # 0xFFFF and shows as "unknown", so the one channel with a plausible reading
    # is the outdoor sensor. Rename it here once that is confirmed, and point
    # OUTDOOR_SENSOR below at it.
    4: [
        "Outdoor card point 1",
        "Outdoor card point 2",
        "Outdoor card point 3",
        "Outdoor card point 4",
        "Outdoor card point 5",
        "Outdoor card point 6",
        "Outdoor card point 7",
        "Outdoor card point 8",
    ],
}

# Set this to whichever AI4 point turns out to be OUTDOOR. Until then nothing
# depends on it; guessing would be worse than leaving it explicit.
OUTDOOR_SENSOR = None

# Per-sensor calibration, in degrees, added after conversion:
#   "sensor.zone_01_temperature": -0.4
#
# For trimming an individual sensor against a reference thermometer. Keep it
# here rather than adjusting the shared 4700 ohm constant or the NTC table:
# those are correct, and changing them to make one room read right throws every
# other room off - and only at some temperatures, which is worse than an offset
# because it looks fixed at the temperature you tested at.
#
# Before adding an entry, check the reading isn't "unknown" - an out-of-range
# resistance now reports unknown rather than clamping, so a sensor that used to
# read an implausible -50 or 120 is a wiring fault, not a calibration problem.
CALIBRATION = {}

# ======================================================
# INPUT_BOOLEAN → BIT MAPPING (CRITICAL)
# ======================================================

OUTPUT_INPUTS = {
    10: [
        "input_boolean.zone_01_heating",
        "input_boolean.zone_02_heating",
        "input_boolean.zone_03_heating",
        "input_boolean.zone_04_heating",
        "input_boolean.zone_05_heating",
        "input_boolean.zone_06_heating",
        "input_boolean.zone_08_heating",
        "input_boolean.zone_11_heating",
    ],
    11: [
        "input_boolean.zone_13_heating",
        "input_boolean.zone_14_heating",
        "input_boolean.zone_15_heating",
        "input_boolean.zone_12_heating",
        "input_boolean.zone_16_heating",
        "input_boolean.zone_17_heating",
        "input_boolean.zone_18_heating",
        "input_boolean.zone_19_heating",
    ],
    12: [
        "input_boolean.zone_10_heating",
        "input_boolean.zone_20_heating",
        "input_boolean.zone_21_heating",
        "input_boolean.zone_22_heating",
        "input_boolean.zone_07_heating",
        "input_boolean.zone_23_heating",
        "input_boolean.zone_24_heating",
        "input_boolean.zone_09_heating",
    ],
    15: [
        "input_boolean.aux_output_1",
        "input_boolean.aux_output_2",
        "input_boolean.aux_output_3",
        "input_boolean.aux_output_4",
        "input_boolean.aux_output_5",
        "input_boolean.aux_output_6",
        "input_boolean.aux_output_7",
        "input_boolean.aux_output_8",
    ],
}

# ======================================================
# SHARED MODULES (READ-MODIFY-WRITE)
# ======================================================
# Modules where we own only *some* bits of the register. The full-register write
# used above would explicitly re-assert 0 on every bit we don't own, so these go
# through _write_shared_register() instead: read the live register, change only
# our bits, write it back.
#
# Keyed by bit index, not a list, so an unowned bit is absent rather than a
# placeholder someone can accidentally fill in.

SHARED_OUTPUT_INPUTS = {
    # Module 20 is plant equipment, not room heating: ventilation units, a solenoid
    # valve, the circulation pump, and an HVAC alarm summary. It is here rather than
    # in OUTPUT_INPUTS above even though we nominally own all 8 bits, because the
    # full-register path asserts HA's view onto the bus at startup - which for a
    # fresh set of input_booleans means writing 0x00 and stopping the plant on the
    # next hot-reload. Going through the shared path makes a restart a no-op.
    #
    # Two things about this card are unverified:
    #   - Bit order is inferred from a prose summary of the FX-2020 panel photos,
    #     not from explicit point numbers the way module 21's was. If it is wrong,
    #     "circulation pump" toggles a ventilation unit instead. Confirm against the
    #     panel before wiring any of these into an automation.
    #   - These points are probably still driven by the FX-2020's own control
    #     program. Expect it to fight back or simply overwrite us.
    20: {
        0: "input_boolean.plant_ahu1_stage1",   # AHU1 point 1
        1: "input_boolean.plant_ahu1_stage2",   # AHU1 point 2
        2: "input_boolean.plant_ahu2_stage1",   # AHU2 point 1
        3: "input_boolean.plant_ahu2_stage2",   # AHU2 point 2
        4: "input_boolean.plant_valve",     # VALVE  solenoid valve
        5: "input_boolean.plant_pump",     # PUMP  circulation pump
        6: "input_boolean.plant_aux",     # AUX
        7: "input_boolean.plant_alarm_summary",     # ALARM_SUM  HVAC alarm summary - see CLAUDE.md
    },
    # Module 21 mixes ordinary loads with legacy points that must not be
    # reachable by accident. All 8 are exposed, on the building owner's explicit
    # instruction. The four marked [interlocked] are gated by INTERLOCKED_BITS
    # below, so they stay controllable but take a deliberate step to reach.
    21: {
        0: "input_boolean.guarded_point_1",         # GP1  alarm continuation  [interlocked]
        1: "input_boolean.shared_load_1",   # LOAD1   appliance enable
        2: "input_boolean.shared_load_2",    # LOAD2   vehicle heater
        3: "input_boolean.guarded_point_2",           # GP2    guarded point      [interlocked]
        4: "input_boolean.guarded_point_3",           # GP3    guarded point      [interlocked]
        5: "input_boolean.guarded_point_4",           # GP4    audible device         [interlocked]
        6: "input_boolean.shared_load_3",    # LOAD3    aux plant 1
        7: "input_boolean.shared_load_4",    # LOAD4    aux plant 2
    },
}

# Bits that are controllable but must not be reachable by accident. Module 21
# bit 0 = GP1 (burglar alarm), 3 = GP2 and 4 = GP3 (front door locks),
# 5 = GP4 (siren).
#
# These were unexposed until 2026-08-01, when the owner confirmed they have no field
# wiring connected. That is a fact about the house today, not a property of this
# code - reconnecting a door lock some future year would not update this file.
# So rather than trusting the wiring, the bits stay behind an interlock: they are
# only written while the named entity is on, and are otherwise left exactly as
# read from the register. Everything else on the card is unaffected.
#
# The interlock is manual on and manual off. An earlier version auto-cleared it
# after 15 minutes; removed on request 2026-08-01, because a control that turns
# itself off is worse than a plain switch when you are stood at the panel trying
# to test something.
#
# Format: module -> (interlock entity, {bits it guards})

INTERLOCKED_BITS = {
    21: ("input_boolean.guarded_points_enabled", {0, 3, 4, 5}),
}

# ======================================================
# HELPERS
# ======================================================

def slug(s):
    s = s.lower()
    for a, b in {"ä": "a", "ö": "o", "å": "a", " ": "_", "-": "_"}.items():
        s = s.replace(a, b)
    return s

def resistance_to_temperature(resistance: float) -> float | None:
    if resistance is None:
        return None

    table = NTC_TABLE

    # Out of range is a fault, not a reading.
    #
    # This used to clamp: anything above the table returned -50 C and anything
    # below returned 120 C. Both are plausible-looking numbers, and both are
    # dangerous. A cut wire measures as a near-infinite resistance, which
    # clamped to -50 C - a thermostat then sees a room 70 degrees below setpoint
    # and heats it continuously, forever. A short clamps to 120 C, and that room
    # never gets heated at all. In winter those are a ruinous bill and frozen
    # pipes respectively, and nothing anywhere would have said a word.
    #
    # The table spans -50..120 C. The site's record low is nowhere near -50, so
    # there is no real reading outside this band - only broken wiring.
    if resistance > table[0][1] or resistance < table[-1][1]:
        return None

    for i in range(len(table) - 1):
        t1, r1 = table[i]
        t2, r2 = table[i + 1]

        if r1 >= resistance >= r2:
            # linear interpolation
            return t1 + (resistance - r1) * (t2 - t1) / (r2 - r1)

    return None

def raw_to_resistance(raw: int) -> float | None:
    """
    Fidelix AI-8 resistive input:
    R = 4700 * raw / (65535 - raw)
    """
    if raw is None or raw <= 0 or raw >= 65535:
        return None

    return 4700.0 * raw / (65535.0 - raw)




# ======================================================
# APP
# ======================================================

class ModbusAllInOne(hass.Hass):

    def initialize(self):
        self.log("=== Modbus All-In-One Controller started ===")

        self.bits = {
            10: [False] * 8,
            11: [False] * 8,
            12: [False] * 8,
            15: [False] * 8,
        }

        self._poll_running = False

        # Modules currently adopting hardware state into HA. Writes are suppressed
        # for these so _adopt_shared_outputs() can't loop back onto the bus.
        self._adopting = set()

        self._init_temp_entities()
        self._init_output_listeners()
        self.run_in(self._initial_sync_outputs, 2)
        self.run_in(self._adopt_shared_outputs, 4)

        self.run_every(
            self._poll_temperatures,
            self.datetime() + timedelta(seconds=POLL_SECONDS),
            POLL_SECONDS,
        )


    # --------------------------------------------------
    # MODBUS CLIENT
    # --------------------------------------------------

    def _client(self):
        """Factory. Do not call directly - go through bus(), which serialises."""
        return ModbusSerialClient(
            port=PORT,
            baudrate=BAUDRATE,
            parity=PARITY,
            stopbits=STOPBITS,
            bytesize=BYTESIZE,
            timeout=TIMEOUT,
        )

    @contextmanager
    def bus(self, what="bus"):
        """Exclusive, connected access to the serial bus, or None.

        Always yields - callers check for None rather than catching. The lock is
        released and the client closed however the block exits.
        """
        if not BUS_LOCK.acquire(timeout=BUS_WAIT_SECONDS):
            self.log(f"[BUS] {what}: busy for {BUS_WAIT_SECONDS}s, skipping",
                     level="WARNING")
            yield None
            return

        client = None
        try:
            client = self._client()
            if not client.connect():
                self.log(f"[BUS] {what}: connect failed", level="WARNING")
                yield None
                return
            yield client
        finally:
            if client is not None:
                client.close()
            BUS_LOCK.release()

    # --------------------------------------------------
    # TEMPERATURES (READ ONLY)
    # --------------------------------------------------

    def _init_temp_entities(self):
        for mod, names in TEMP_NAMES.items():
            for idx, name in enumerate(names):
                self.set_state(
                    f"sensor.{slug(name)}",
                    state="unknown",
                    attributes={
                        "friendly_name": name,
                        "unit_of_measurement": "°C",
                        "device_class": "temperature",
                        # Without state_class, Home Assistant computes no
                        # long-term statistics for this sensor - so everything
                        # older than the recorder purge window (10 days by
                        # default) is gone forever. These sensors have been
                        # running for months and none of it was kept.
                        "state_class": "measurement",
                    },
                )

    def _poll_temperatures(self, kwargs):
        if self._poll_running:
            self.log("Temperature poll skipped (previous still running)", level="WARNING")
            return

        self._poll_running = True
        start = time.time()
        ok = 0

        try:
            with self.bus("temperature poll") as client:
                if client is None:
                    return

            for mod, names in TEMP_NAMES.items():
                try:
                    rr = client.read_holding_registers(
                        address=TEMP_BASE_ADDR,
                        count=8,
                        device_id=mod,
                    )

                    if rr and not rr.isError():
                        ok += 1
                        for i, raw in enumerate(rr.registers):
                            ent = f"sensor.{slug(names[i])}"

                            if raw in INVALID_RAW:
                                self.set_state(ent, state="unknown")
                            else:
                                resistance = raw_to_resistance(raw)
                                temperature = resistance_to_temperature(resistance)

                                if temperature is None:
                                    # Out of range: open circuit, short, or a
                                    # sensor that isn't there. Reporting unknown
                                    # rather than a clamped extreme is what stops
                                    # the thermostat acting on a fault.
                                    self.log(
                                        f"[TEMP] {ent} out of range "
                                        f"(raw={raw}, R={resistance:.0f} ohm) -> unknown",
                                        level="WARNING",
                                    )
                                    self.set_state(ent, state="unknown")
                                else:
                                    offset = CALIBRATION.get(ent, 0.0)
                                    self.set_state(
                                        ent,
                                        state=round(temperature + offset, 2),
                                        attributes={
                                            "unit_of_measurement": "°C",
                                            "device_class": "temperature",
                                            "state_class": "measurement",
                                            "resistance_ohm": round(resistance, 1),
                                            "raw": raw,
                                            "calibration_offset": offset,
                                        },
                                    )

                except Exception as e:
                    self.log(f"[TEMP ERROR] mod={mod} {e}", level="WARNING")

        finally:
            elapsed = time.time() - start
            if elapsed > POLL_SECONDS:
                self.log(f"Temperature poll took {elapsed:.1f}s", level="WARNING")

            self._poll_running = False

            # Heartbeat for system_health.py. Sensor staleness alone is not a
            # reliable signal - a stuck poll leaves the last temperatures sitting
            # there looking plausible, which is exactly how a jammed thread went
            # unnoticed for two days on 2026-07-29. This says when the bus was
            # last actually read, separately from what it said.
            self.set_state(
                HEARTBEAT_SENSOR,
                state=self.datetime().isoformat(timespec="seconds"),
                attributes={
                    "friendly_name": "Modbus - last read",
                    "icon": "mdi:heart-pulse",
                    "modules_ok": ok,
                    "modules_expected": len(TEMP_NAMES),
                    "duration_s": round(elapsed, 2),
                },
            )


    # --------------------------------------------------
    # OUTPUT CONTROL (NODE-RED STYLE)
    # --------------------------------------------------

    def _init_output_listeners(self):
        for mod, entities in OUTPUT_INPUTS.items():
            for bit, entity in enumerate(entities):
                self.listen_state(
                    self._input_changed,
                    entity,
                    module=mod,
                    bit=bit,
                )

        for mod, bits in SHARED_OUTPUT_INPUTS.items():
            for bit, entity in bits.items():
                self.listen_state(
                    self._shared_input_changed,
                    entity,
                    module=mod,
                    bit=bit,
                )

        for mod, (entity, _bits) in INTERLOCKED_BITS.items():
            self.listen_state(self._interlock_changed, entity, module=mod)

    def _initial_sync_outputs(self, kwargs):
        self.log("Initial sync from HA → Modbus")

        for mod, entities in OUTPUT_INPUTS.items():
            for bit, entity in enumerate(entities):
                state = self.get_state(entity)
                self.bits[mod][bit] = (state == "on")
                self.log(f"[INIT] {entity} = {state} (mod {mod} bit {bit})")

            self._write_register(mod)

    def _input_changed(self, entity, attribute, old, new, kwargs):
        if old == new or new not in ("on", "off"):
            return

        mod = kwargs["module"]
        bit = kwargs["bit"]

        self.bits[mod][bit] = (new == "on")

        self.log(
            f"[INPUT] {entity} {old} -> {new} (module {mod} bit {bit})"
        )

        self._write_register(mod)

    # --------------------------------------------------
    # SHARED OUTPUT CONTROL (READ-MODIFY-WRITE)
    # --------------------------------------------------

    def _adopt_shared_outputs(self, kwargs):
        """Startup direction for shared modules is hardware → HA, not HA → hardware.

        _initial_sync_outputs() asserts HA's view onto the bus, which is fine for
        modules we own outright. Doing that here would mean an AppDaemon restart -
        including the hot-reload a plain `git pull` triggers - physically switching
        equipment we only partly own, unattended, from input_booleans that default
        to off. Reading the live register and reflecting it into HA instead makes a
        restart a no-op in the house.
        """
        for mod, bits in SHARED_OUTPUT_INPUTS.items():
            value = self._read_register(mod)
            if value is None:
                self.log(
                    f"[ADOPT] module={mod} read failed; leaving HA state alone",
                    level="WARNING",
                )
                continue

            self._adopting.add(mod)
            try:
                for bit, entity in bits.items():
                    on = bool(value & (1 << bit))
                    self.log(f"[ADOPT] {entity} <- {'on' if on else 'off'} (mod {mod} bit {bit})")
                    if on:
                        self.turn_on(entity)
                    else:
                        self.turn_off(entity)
            finally:
                self._adopting.discard(mod)

    def _shared_input_changed(self, entity, attribute, old, new, kwargs):
        if old == new or new not in ("on", "off"):
            return

        mod = kwargs["module"]

        if mod in self._adopting:
            self.log(f"[SHARED] {entity} {old} -> {new} ignored (adopting module {mod})")
            return

        bit = kwargs["bit"]

        # A guarded toggle with the interlock closed would silently do nothing and
        # leave the toggle showing a state the hardware isn't in. Put it back.
        if (1 << bit) & self._interlocked_mask(mod) and not self._interlock_open(mod):
            self.log(
                f"[SHARED] {entity} {old} -> {new} refused: interlock closed. "
                f"Reverting the toggle.",
                level="WARNING",
            )
            self._adopting.add(mod)
            try:
                if old == "on":
                    self.turn_on(entity)
                else:
                    self.turn_off(entity)
            finally:
                self._adopting.discard(mod)
            return

        self.log(f"[SHARED] {entity} {old} -> {new} (module {mod} bit {bit})")
        self._write_shared_register(mod)

    def _write_shared_register(self, mod):
        current = self._read_register(mod)
        if current is None:
            self.log(
                f"[SHARED WRITE] module={mod} aborted: cannot read current register",
                level="WARNING",
            )
            return

        unlocked = self._interlock_open(mod)
        guarded = self._interlocked_mask(mod)

        # Which bits this write would actually apply from HA state.
        applying = {
            bit: entity
            for bit, entity in SHARED_OUTPUT_INPUTS[mod].items()
            if unlocked or not ((1 << bit) & guarded)
        }

        # Every one of them must have a real state first. A missing or unavailable
        # entity is not "off" - reading it as off would clear a bit belonging to
        # equipment that is currently running. This is a live hazard, not theory:
        # AppDaemon hot-reloads on `git pull`, so the app can be running against a
        # HA that has not yet been told about newly added input_booleans, and every
        # one of them reads back None.
        missing = sorted(
            f"{entity}={self.get_state(entity)!r}"
            for entity in applying.values()
            if self.get_state(entity) not in ("on", "off")
        )
        if missing:
            self.log(
                f"[SHARED WRITE] module={mod} aborted: {len(missing)} owned "
                f"entities have no usable state ({', '.join(missing)}). "
                f"Reload the input_boolean YAML and restart AppDaemon.",
                level="ERROR",
            )
            return

        value = current
        for bit, entity in SHARED_OUTPUT_INPUTS[mod].items():
            if bit not in applying:
                # Leave the bit exactly as the hardware reported it.
                self.log(
                    f"[SHARED WRITE] module={mod} bit={bit} held: interlock closed, "
                    f"{entity} not applied",
                    level="WARNING",
                )
                continue

            if self.get_state(entity) == "on":
                value |= (1 << bit)
            else:
                value &= ~(1 << bit)

        changed = value ^ current

        # Belt and braces: whatever the loop above did, a closed interlock must
        # leave every guarded bit untouched.
        if not unlocked and (changed & guarded):
            self.log(
                f"[SHARED WRITE] module={mod} aborted: write would change "
                f"interlocked bits with the interlock closed "
                f"(mask 0b{changed & guarded:08b})",
                level="ERROR",
            )
            return

        if changed & guarded:
            self.log(
                f"[SHARED WRITE] module={mod} driving SECURITY points "
                f"(mask 0b{changed & guarded:08b}) - interlock is open",
                level="WARNING",
            )

        if not changed:
            self.log(f"[SHARED WRITE] module={mod} already at {current}; no write")
            return

        self.log(
            f"[SHARED WRITE] module={mod} {current} -> {value} "
            f"(changed 0b{changed:08b})"
        )

        with self.bus(f"write module {mod}") as client:
            if client is None:
                return
            client.write_register(
                address=OUTPUT_BASE_ADDR,
                value=value,
                device_id=mod,
            )

    def _interlocked_mask(self, mod):
        if mod not in INTERLOCKED_BITS:
            return 0

        mask = 0
        for bit in INTERLOCKED_BITS[mod][1]:
            mask |= (1 << bit)
        return mask

    def _interlock_open(self, mod):
        """True only if this module's interlock entity is explicitly on.

        Anything else - off, unavailable, missing, or a module with no interlock
        entry whose bits are somehow guarded - reads as closed.
        """
        if mod not in INTERLOCKED_BITS:
            return True

        entity = INTERLOCKED_BITS[mod][0]
        return self.get_state(entity) == "on"

    def _interlock_changed(self, entity, attribute, old, new, kwargs):
        """Log interlock transitions. The interlock is manual - it stays as set."""
        if old == new or new not in ("on", "off"):
            return

        mod = kwargs["module"]
        self.log(
            f"[INTERLOCK] {entity} {'OPENED' if new == 'on' else 'closed'} "
            f"for module {mod}",
            level="WARNING",
        )

    def _read_register(self, mod):
        with self.bus(f"read module {mod}") as client:
            if client is None:
                return None
            try:
                rr = client.read_holding_registers(
                    address=OUTPUT_BASE_ADDR,
                    count=1,
                    device_id=mod,
                )
                if not rr or rr.isError():
                    self.log(f"[READ] module={mod} error: {rr}", level="WARNING")
                    return None
                return rr.registers[0]
            except Exception as e:
                self.log(f"[READ ERROR] mod={mod} {e}", level="WARNING")
                return None

    def _write_register(self, mod):
        value = 0
        for bit in range(8):
            if self.bits[mod][bit]:
                value |= (1 << bit)

        self.log(f"[WRITE] module={mod} register={OUTPUT_BASE_ADDR} value={value}")

        with self.bus(f"full write module {mod}") as client:
            if client is None:
                return
            client.write_register(
                address=OUTPUT_BASE_ADDR,
                value=value,
                device_id=mod,
            )

"""Notice when part of this system quietly stops working.

Every fault found on 2026-08-01 had been failing silently, some for days:

    - the Nord Pool template sensor pointed at an entity that no longer existed,
      so price optimisation had been running on nothing
    - the Modbus polling thread had been wedged since 2026-07-29 13:51, which
      also froze real heating-relay writes, because they share that thread
    - a wrong summer transfer tariff
    - a dashboard reading the same dead entity

Nothing in the system would have reported any of them. They were found by
someone happening to read the code. This app is the missing piece: it watches
the things that go quiet and says so.

Deliberately dumb. It checks ages, not values - "when did this last update"
rather than "is this number sensible". Age is the failure mode that has actually
occurred here, repeatedly, and a stale reading looks perfectly plausible sitting
on a dashboard.

Read-only apart from persistent notifications.
"""
import appdaemon.plugins.hass.hassapi as hass
from datetime import datetime, timedelta, timezone

CHECK_INTERVAL = 60

# entity -> (label, max age before it counts as stale)
#
# Thresholds are generous multiples of each source's real cadence. The point is
# catching "stopped entirely", not jitter: a Modbus poll runs every 30 s, so 5
# minutes means roughly ten missed polls in a row.
WATCHED = {
    "sensor.modbus_last_read": ("Modbus bus read", timedelta(minutes=5)),
    # Read-back of the DO registers, every minute. Stale means outputs are no
    # longer being checked against HA - see modbus_bridge VERIFY_SECONDS.
    "sensor.modbus_output_check": ("Output read-back", timedelta(minutes=5)),
    "sensor.spot_prices": ("Spot prices", timedelta(hours=6)),
    "sensor.pv_production": ("PV production", timedelta(minutes=30)),
    "sensor.grid_power": ("Grid meter", timedelta(minutes=15)),
}

# Sources that are allowed to be missing entirely without it being a fault -
# nothing here is load-bearing for heating, and not every install has them.
# "Missing" means the entity does not exist. One that exists and reads
# unavailable is a fault like any other: an integration can stop retrying after
# its device reboots, and for days it showed as "not in use" instead of a
# problem.
OPTIONAL = {"sensor.pv_production", "sensor.grid_power"}

# Unavailable for longer than its WATCHED limit -> reload its integration, at
# most once per SELF_HEAL_EVERY. A reload is what fixed the grid meter
# integration once; it touches nothing but that integration.
SELF_HEAL = {"sensor.grid_power", "sensor.pv_production"}

# Sources that are legitimately unavailable in the dark: the inverter sleeps
# from dusk to dawn and its entity goes unavailable every night. In daylight
# the same state is a fault - the integration once silently lost the inverter
# for weeks and nothing said so.
DARK_OK = {"sensor.pv_production"}
DARK_BELOW_ELEVATION = 3.0
SELF_HEAL_EVERY = timedelta(hours=1)

STATUS_SENSOR = "sensor.jarjestelman_tila"
NOTIFICATION_ID = "home_heating_health"

OK = "ok"
WARNING = "varoitus"
FAULT = "vika"


class SystemHealth(hass.Hass):

    def initialize(self):
        self.log("=== SystemHealth started ===")
        self._notified = False
        self._last_heal = {}
        self.run_every(self.check, self.datetime(), CHECK_INTERVAL)

    # --------------------------------------------------

    def _age_of(self, entity):
        """Seconds since this entity last updated, or None if it can't be read."""
        info = self.get_state(entity, attribute="all")
        if not info:
            return None

        if info.get("state") in ("unknown", "unavailable"):
            return None

        return self._seconds_since(info.get("last_updated") or info.get("last_changed"))

    def _seconds_since(self, stamp):
        if not stamp:
            return None

        try:
            if isinstance(stamp, str):
                stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None

        # HA's stamps are UTC and carry their offset; compare them with a UTC
        # clock. Stripping the offset and comparing with AppDaemon's naive
        # local time, as this used to, added AppDaemon's UTC offset to every
        # age: two hours under a wrong timezone setting, so the 5-minute Modbus
        # and 30-minute inverter limits were always exceeded and the status
        # read vika permanently.
        if stamp.tzinfo is not None:
            return (self._utc_now() - stamp).total_seconds()

        return (self.datetime() - stamp).total_seconds()

    def _utc_now(self):
        return datetime.now(timezone.utc)

    # --------------------------------------------------

    def _dead_temperature_sensors(self):
        """Room sensors reading unknown while the bus is demonstrably alive.

        Since 2026-08-01 an out-of-range resistance reports unknown instead of
        clamping to -50 or 120 C, which is what stops a thermostat acting on a
        cut wire. But unknown is only useful if somebody hears about it - and a
        single dead sensor among 32 is easy to miss on a dashboard.

        Only counted when the Modbus heartbeat is fresh: if the whole bus is
        down every sensor reads unknown, and that is already reported above as
        one problem rather than thirty.
        """
        heartbeat_age = self._age_of("sensor.modbus_last_read")
        if heartbeat_age is None or heartbeat_age > 300:
            return []

        dead = []
        for entity in self.get_state("sensor") or {}:
            if not entity.endswith("_temperature"):
                continue
            if self.get_state(entity) in ("unknown", "unavailable"):
                dead.append(entity)
        return sorted(dead)

    def _sun_down(self):
        elevation = self.get_state("sun.sun", attribute="elevation")
        return isinstance(elevation, (int, float)) and elevation < DARK_BELOW_ELEVATION

    def _self_heal(self, entity, label):
        if entity not in SELF_HEAL:
            return
        now = self.datetime()
        last = self._last_heal.get(entity)
        if last is not None and now - last < SELF_HEAL_EVERY:
            return
        self._last_heal[entity] = now
        self.log(f"[HEALTH] {label} unavailable - reloading its integration", level="WARNING")
        try:
            self.call_service("homeassistant/reload_config_entry", entity_id=entity)
        except Exception as e:
            self.log(f"[HEALTH] reload of {entity} failed: {e}", level="WARNING")

    def check(self, kwargs):
        problems = []
        details = {}

        dead = self._dead_temperature_sensors()
        if dead:
            details["Temperature sensors"] = f"{len(dead)} not reading"
            problems.append(
                f"{len(dead)} temperature sensors give no reading while the bus is "
                f"working (cut wire or short): {', '.join(dead[:5])}"
                + (" ..." if len(dead) > 5 else "")
            )

        for entity, (label, max_age) in WATCHED.items():
            info = self.get_state(entity, attribute="all")
            if isinstance(info, dict) and info.get("state") in ("unavailable", "unknown"):
                if entity in DARK_OK and self._sun_down():
                    details[label] = "night"
                    continue
                down = self._seconds_since(info.get("last_changed"))
                minutes = (down or 0) / 60.0
                details[label] = f"unavailable {minutes:.0f} min"
                if down is None or down > max_age.total_seconds():
                    problems.append(f"{label}: unavailable {minutes:.0f} min")
                    self._self_heal(entity, label)
                continue

            age = self._age_of(entity)

            if age is None:
                if entity in OPTIONAL:
                    details[label] = "not in use"
                    continue
                details[label] = "NO DATA"
                problems.append(f"{label}: no data at all")
                continue

            minutes = age / 60.0
            stale = age > max_age.total_seconds()
            # At dusk and dawn the inverter is awake but reports a constant 0 W, and a
            # value that doesn't change doesn't refresh its timestamp. Not a fault while
            # the sun is this low.
            if stale and entity in DARK_OK and self._sun_down():
                details[label] = f"{minutes:.0f} min ago (sun low)"
                continue
            details[label] = f"{minutes:.0f} min ago" + (" - STALE" if stale else "")

            if stale:
                problems.append(
                    f"{label}: last updated {minutes:.0f} min ago "
                    f"(limit {max_age.total_seconds() / 60:.0f} min)"
                )

        state = FAULT if problems else OK

        self.set_state(
            STATUS_SENSOR,
            state=state,
            attributes={
                "friendly_name": "System status",
                "icon": "mdi:heart-pulse" if state == OK else "mdi:alert",
                "problems": problems,
                "tarkistukset": details,
                "tarkistettu": self.datetime().isoformat(timespec="seconds"),
            },
        )

        self._notify(problems)

    def _notify(self, problems):
        """One notification per episode, not one per minute."""
        if problems and not self._notified:
            self.log(f"[HEALTH] {len(problems)} problem(s): {problems}", level="ERROR")
            self.call_service(
                "persistent_notification/create",
                title="Heating system: check",
                message=(
                    "Part of the system is not updating:\n\n- "
                    + "\n- ".join(problems)
                    + "\n\nKatso AppDaemonin loki."
                ),
                notification_id=NOTIFICATION_ID,
            )
            self._notified = True

        elif not problems and self._notified:
            self.log("[HEALTH] recovered", level="INFO")
            self.call_service(
                "persistent_notification/dismiss",
                notification_id=NOTIFICATION_ID,
            )
            self._notified = False

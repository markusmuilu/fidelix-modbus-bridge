"""Give it a temperature. It keeps the room there, as cheaply as it can.

This replaces what PriceOptimised used to mean. The old behaviour was: pick the
N cheapest 15-minute slots of the day and heat during them, where N is a number
the user typed in per room. Four things were wrong with that, and the worst is
the last:

  - N is a guess, and it was the *user's* guess. Nobody knows how many hours a
    room needs; it depends on weather, sun and what the room did yesterday.
  - Slots were chosen by price alone, ignoring when the heat was needed.
  - It knew nothing about the weather.
  - It was open-loop on temperature. The thermostat was switched off entirely,
    so nothing checked the room was actually warm. It was a scheduler, not a
    controller, and it could leave a room at 15 C through a cold snap.

HOW THIS WORKS INSTEAD

Set a target and a flex band. The room is allowed to float between
target-flex and target+flex, and where inside that band it aims depends on how
cheap electricity is right now, relative to the rest of the day:

    rank = 0.0   cheapest slot in the horizon    ->  aim high  (target + flex)
    rank = 1.0   dearest slot in the horizon     ->  aim low   (target - flex)

    effective = (target - flex) + 2*flex*(1 - rank)

So on cheap power it deliberately overshoots, storing heat in the building's
mass; on expensive power it coasts down through the band and spends nothing.
That is thermal-mass arbitrage, and the thermostat is doing it rather than a
schedule.

WHY THIS NEEDS NO THERMAL MODEL

Because it is closed-loop. Two consequences worth being explicit about:

  - **Solar gain handles itself.** the author's observation that 20 C rainy and
    20 C sunny are different houses is exactly right, and a feedback controller
    does not care: if the sun warms the room, the measured temperature rises and
    the controller simply stops calling for heat. Predicting the disturbance is
    only needed to *anticipate* it.
  - **A leaky room self-corrects.** No R, no C, no fitted coefficients. A room
    that loses heat fast just asks for more, more often.

What a fitted model would add later is anticipation - preheating ahead of an
expensive block rather than reacting once inside it. That is worth maybe a few
percent on top. This is the part worth having first.

THE FLOOR IS NEVER NEGOTIABLE

At rank 1.0 the effective target is target-flex, not "off". However expensive
power gets, the room never drops below the floor the user set. That is what
fixes the old open-loop flaw, and it means a bad price feed degrades to
"slightly more expensive", never to "cold house".
"""
import appdaemon.plugins.hass.hassapi as hass
from datetime import datetime, timedelta, timezone

from heating_rooms import LOOPS, element_of
from heating_mode import mode_of, OPTIMIZER

CONTROL_INTERVAL = 60

PRICE_SENSOR = "sensor.spot_prices"

# Slots ahead to rank against. A full day, so "cheap" means cheap relative to
# what is actually coming rather than to the last hour.
HORIZON_SLOTS = 96

# Deadband around the effective target, to stop the relay chattering.
DEADBAND = 0.3

DEFAULT_FLEX = 1.0

# ---- Per-phase cap ----
#
# The owner wants the main fuses down to 3x25 A, about 5.75 kW per phase. Before
# this, one cheap slot raised the aim in every price room at once and they all
# switched on together; one phase alone carried 8.5 kW of elements.
#
# Two thresholds, read against the live grid meter, which also sees what no plan
# does (oven, kettle, a night-time load that cycles on one phase):
#   - a room may switch on only if the meter reading on its phase + its element stays under
#     PHASE_LIMIT_W. When several rooms want heat, the one furthest below its
#     aim goes first; the rest wait for the next minute.
#   - if a phase stays over PHASE_SHED_W for SHED_AFTER cycles, the optimiser
#     room on it that is closest to its aim is switched off, one per cycle.
# Only price-optimised rooms are capped; this app owns no other relay.
#
# The 5.0 kW limit is a starting figure, to be checked after a week or two
# against the meter's peaks.
PHASE_SENSORS = {
    1: "sensor.grid_power_phase_1",
    2: "sensor.grid_power_phase_2",
    3: "sensor.grid_power_phase_3",
}
PHASE_LIMIT_W = 5000
PHASE_SHED_W = 5750
SHED_AFTER = 2

# A meter reading older than this counts as missing. Without it, at most one room switches
# on per cycle (staggering), so a cheap slot can't add every element at once.
METER_STALE_S = 120
STARTS_WITHOUT_METER = 1

# Census numbers are padded by how sure they are. An over-estimate only makes
# a room wait a minute; an under-estimate is what blows a fuse.
MARGIN = {"label": 1.0, "measured": 1.1, "single": 1.15, "estimate": 1.25}

# A room with no known element (drew nothing in the census) may only start when
# every phase has this much room, since its phase is unknown too.
UNKNOWN_ELEMENT_W = 1500

# ---- Solar ----
#
# Own surplus is worth the full buying price (spot + transfer + tax); exported it
# earns only spot. Two parts, and the meter has the last word in both:
#
#   LIVE. While the meter shows export, a room may heat up to the TOP of its band
#   (target + flex), as long as its element fits in the export. These are
#   "solar rooms". If the house imports more than SOLAR_IMPORT_ALLOW_W for
#   SOLAR_STOP_AFTER cycles (a cloud, the oven), solar rooms are switched off
#   one per cycle, warmest first. Solar never lowers anything: a room below its
#   normal aim heats on grid power as before.
#
#   HOLDBACK. If pv_forecast expects HOLDBACK_MIN_KWH of usable surplus within
#   HOLDBACK_HOURS, the grid-power aim is capped at the target: no overshoot on
#   bought power when the sun will do it for free. The floor is untouched, so
#   the worst a wrong forecast can do is leave a room at target instead of
#   above it.
#
# Forecast error handling: the forecast is ignored when it is missing or older
# than FORECAST_MAX_AGE_H, and distrusted for the rest of the day when the
# inverter falls well short of it - either over the day's completed hours
# (under TRUST_MIN_RATIO of forecast, once TRUST_MIN_FORECAST_KWH was expected)
# or within the current hour (under HOUR_CHECK_RATIO of a >= HOUR_CHECK_MIN_KW
# forecast after 30 min). Distrust only switches the holdback off; the live
# part never used the forecast. Over-production needs no handling: the live
# part sees the export.
GRID_SENSOR = "sensor.grid_power"              # W, negative = exporting
PV_SENSOR = "sensor.pv_production"             # W
SOLAR_PLAN = "sensor.solar_plan"               # from a forecast app, not included here
SOLAR_STATUS = "sensor.heating_and_solar"
SOLAR_IMPORT_ALLOW_W = 200
SOLAR_STOP_AFTER = 2
SOLAR_MIN_PV_W = 300
GRID_STALE_S = 120
HOLDBACK_HOURS = 6
HOLDBACK_MIN_KWH = 1.5
FORECAST_MAX_AGE_H = 3
TRUST_MIN_FORECAST_KWH = 1.0
TRUST_MIN_RATIO = 0.5
HOUR_CHECK_MIN_KW = 1.0
HOUR_CHECK_RATIO = 0.25


def flex_of(room):
    return f"input_number.{room}_flex"


# Transfer tariff, EUR/kWh, added to the spot price before ranking - what you
# pay is spot plus transfer, and a tariff with a day/night structure can reorder
# which slots are actually cheapest. REDACTED: the real values are contract
# specific. Read them off your own bill.
DAY_TRANSFER = 0.05
NIGHT_TRANSFER = 0.03


class HeatingSmart(hass.Hass):

    def initialize(self):
        self.log("=== HeatingSmart started ===")
        self._ranks = {}
        self._over = {p: 0 for p in PHASE_SENSORS}
        self._solar_rooms = set()
        self._import_cycles = 0
        self._pv_wh = {}          # hour start (UTC) -> [Wh so far, samples]
        self._fc_kw = {}          # hour start (UTC) -> forecast kW, as last seen before it began
        self._distrust = None     # (local date, reason) while the forecast is distrusted
        self.listen_state(self.recalculate, PRICE_SENSOR)
        self.run_every(self.control, self.datetime(), CONTROL_INTERVAL)
        self.recalculate(None)

    # --------------------------------------------------

    @staticmethod
    def slot_key(dt):
        return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0,
                          tzinfo=None)

    def transfer_price(self, ts):
        """the seasonal transfer tariff: flat in summer, day/night split Nov-Mar."""
        winter = ts.month >= 11 or ts.month <= 3
        if winter and ts.weekday() <= 5 and 7 <= ts.hour < 22:
            return DAY_TRANSFER
        return NIGHT_TRANSFER

    def recalculate(self, *args):
        """Rank every upcoming slot from 0 (cheapest) to 1 (dearest)."""
        sensor = self.get_state(PRICE_SENSOR, attribute="all")
        if not sensor:
            self.log("No spot price data - holding previous ranks", level="WARNING")
            return

        times = sensor["attributes"].get("times", [])
        prices = sensor["attributes"].get("prices", [])

        now_key = self.slot_key(datetime.now())
        upcoming = []
        for t, p in zip(times, prices):
            try:
                ts = datetime.fromisoformat(t)
            except (ValueError, TypeError):
                continue
            key = self.slot_key(ts)
            if key >= now_key:
                upcoming.append((key, p + self.transfer_price(ts)))

        upcoming = sorted(upcoming, key=lambda x: x[0])[:HORIZON_SLOTS]
        if len(upcoming) < 2:
            self.log("Too few upcoming slots to rank", level="WARNING")
            return

        by_price = sorted(upcoming, key=lambda x: x[1])
        last = len(by_price) - 1
        self._ranks = {key: i / last for i, (key, _) in enumerate(by_price)}

        cheapest = by_price[0]
        dearest = by_price[-1]
        self.log(
            f"[SMART] ranked {len(upcoming)} slots: "
            f"cheapest {cheapest[0]:%H:%M} @ {cheapest[1]*100:.1f} snt, "
            f"dearest {dearest[0]:%H:%M} @ {dearest[1]*100:.1f} snt"
        )

    def current_rank(self):
        """0 = cheapest slot of the horizon, 1 = dearest. None if unknown."""
        return self._ranks.get(self.slot_key(datetime.now()))

    # --------------------------------------------------

    def _number(self, entity, default):
        try:
            return float(self.get_state(entity))
        except (TypeError, ValueError):
            return default

    def effective_target(self, target, flex, rank):
        """Where in the band to aim, given how cheap this slot is."""
        low = target - flex
        if rank is None:
            # No price information. Aim at the floor: never cold, never
            # speculatively expensive. Failing safe here means failing cheap.
            return low
        return low + 2 * flex * (1 - rank)

    def control(self, kwargs):
        rank = self.current_rank()
        solar = self._solar()
        acted = 0
        waiting = []          # (deficit, room, cfg, aim, temp, target, flex, note)
        sunny = []            # same shape; rooms that would heat only on surplus

        for room, cfg in LOOPS.items():
            mode = mode_of(self, room)
            target = self._number(cfg["setpoint"], 21.0)
            flex = self._number(flex_of(room), DEFAULT_FLEX)

            if mode != OPTIMIZER:
                self._solar_rooms.discard(room)
                self._publish_target(
                    room, None, target, flex, rank,
                    reason=f"{mode} owns this output - no price control",
                )
                continue

            try:
                temp = float(self.get_state(cfg["temp"]))
            except (TypeError, ValueError):
                # No temperature means no closed loop. Leave the relay where it
                # is rather than guessing - the old code's failure mode was
                # acting on data it did not have.
                self._solar_rooms.discard(room)
                self._publish_target(
                    room, None, target, flex, rank,
                    reason="Temperature unreadable - output left as it was",
                )
                continue

            aim = self.effective_target(target, flex, rank)
            note = None
            if solar["holdback"] and aim > target:
                aim = target
                note = f"sun expected at {solar['holdback']} - no preheating on bought power"
            top = target + flex
            on = self.get_state(cfg["relay"]) == "on"
            acted += 1

            if temp < aim - DEADBAND:
                # Needs heat whatever the source. Switching on goes through the cap.
                self._solar_rooms.discard(room)
                if not on:
                    waiting.append((aim - temp, room, cfg, aim, temp, target, flex, note))
                    continue
            elif on and temp > aim + DEADBAND:
                if solar["live"] and temp < top and (
                        room in self._solar_rooms or solar["export_w"] > 0):
                    self._solar_rooms.add(room)
                    note = self._solar_note(top)
                else:
                    self._solar_rooms.discard(room)
                    self.call_service("input_boolean/turn_off", entity_id=cfg["relay"])
            elif not on and solar["live"] and temp < top - DEADBAND:
                sunny.append((top - temp, room, cfg, aim, temp, target, flex, note))
                continue
            elif on and room in self._solar_rooms:
                note = self._solar_note(top)

            self._publish_target(
                room, aim, target, flex, rank,
                temperature=temp,
                reason=self._explain(rank, aim, temp, note),
            )

        loads = self._admit(waiting, rank)
        self._admit_solar(sunny, solar, loads, rank)
        self._shed(rank)
        self._solar_stop(solar, rank)
        self._publish_solar(solar)

        if acted:
            self.log(
                f"[SMART] {acted} room(s), price rank "
                f"{'unknown' if rank is None else f'{rank:.2f}'}"
                + (f", solar rooms {sorted(self._solar_rooms)}" if self._solar_rooms else "")
            )

    @staticmethod
    def _solar_note(top):
        return f"surplus solar - storing free heat, toward {top:.1f} C"

    # ---- per-phase cap --------------------------------

    def _phase_loads(self):
        """Live W per phase from the meter, or None if any phase is missing or stale."""
        loads = {}
        now = datetime.now(timezone.utc)
        for phase, entity in PHASE_SENSORS.items():
            try:
                loads[phase] = float(self.get_state(entity))
                updated = datetime.fromisoformat(
                    self.get_state(entity, attribute="last_updated"))
            except (TypeError, ValueError):
                return None
            if (now - updated).total_seconds() > METER_STALE_S:
                return None
        return loads

    @staticmethod
    def cap_watts(room):
        """(phase or None, padded W) the cap books for a room's element."""
        el = element_of(room)
        if el is None:
            return None, UNKNOWN_ELEMENT_W
        return el["phase"], el["watts"] * MARGIN.get(el["source"], 1.25)

    def _admit(self, waiting, rank):
        """Switch on the coldest rooms first, as far as each phase has room."""
        loads = self._phase_loads()
        started = 0

        for deficit, room, cfg, aim, temp, target, flex, note in sorted(
                waiting, key=lambda w: w[0], reverse=True):
            phase, watts = self.cap_watts(room)

            if loads is None:
                ok = started < STARTS_WITHOUT_METER
                why = "meter reading missing - starting one room at a time"
            elif phase is None:
                ok = all(l + watts <= PHASE_LIMIT_W for l in loads.values())
                why = "element phase unknown - waiting for room on every phase"
            else:
                ok = loads[phase] + watts <= PHASE_LIMIT_W
                why = (f"phase L{phase} full ({loads[phase]:.0f} W) - "
                       f"waiting its turn")

            if ok:
                self.call_service("input_boolean/turn_on", entity_id=cfg["relay"])
                started += 1
                if loads is not None:
                    for p in ([phase] if phase else loads):
                        loads[p] += watts
                reason = self._explain(rank, aim, temp, note)
            else:
                self.log(f"[CAP] {room} held: {why}")
                reason = f"{self._explain(rank, aim, temp, note)}; {why}"

            self._publish_target(room, aim, target, flex, rank,
                                 temperature=temp, reason=reason)

        self._started_w = sum(self.cap_watts(r)[1] for _, r, *_ in waiting
                              if self.get_state(LOOPS[r]["relay"]) == "on")
        return loads

    def _shed(self, rank):
        """Over the fuse figure for SHED_AFTER cycles: drop one room per cycle."""
        loads = self._phase_loads()
        if loads is None:
            return

        for phase, load in loads.items():
            self._over[phase] = self._over[phase] + 1 if load > PHASE_SHED_W else 0
            if self._over[phase] < SHED_AFTER:
                continue

            # Optimiser rooms heating on this phase, warmest relative to aim first.
            candidates = []
            for room, cfg in LOOPS.items():
                if (mode_of(self, room) != OPTIMIZER
                        or self.get_state(cfg["relay"]) != "on"
                        or self.cap_watts(room)[0] != phase):
                    continue
                try:
                    temp = float(self.get_state(cfg["temp"]))
                except (TypeError, ValueError):
                    temp = None
                target = self._number(cfg["setpoint"], 21.0)
                flex = self._number(flex_of(room), DEFAULT_FLEX)
                margin = (temp - self.effective_target(target, flex, rank)
                          if temp is not None else 0.0)
                candidates.append((margin, room, cfg))

            if not candidates:
                self.log(f"[CAP] L{phase} at {load:.0f} W, nothing of ours to shed",
                         level="WARNING")
                continue

            margin, room, cfg = max(candidates, key=lambda c: c[0])
            self.log(f"[CAP] L{phase} at {load:.0f} W for {self._over[phase]} "
                     f"cycles: shedding {room}", level="WARNING")
            self.call_service("input_boolean/turn_off", entity_id=cfg["relay"])
            self._over[phase] = 0

    # ---- solar -----------------------------------------

    def _reading(self, entity, max_age_s):
        """Float state of entity, or None if missing, unparseable or stale."""
        try:
            value = float(self.get_state(entity))
            updated = datetime.fromisoformat(self.get_state(entity, attribute="last_updated"))
        except (TypeError, ValueError):
            return None
        if (datetime.now(timezone.utc) - updated).total_seconds() > max_age_s:
            return None
        return value

    def _read_plan(self, now):
        """(pv_kw, use_surplus_kw), both {hour start UTC: kW}, or None if unusable."""
        plan = self.get_state(SOLAR_PLAN, attribute="all")
        if not isinstance(plan, dict):
            return None
        attrs = plan.get("attributes") or {}
        try:
            issued = datetime.fromisoformat(attrs["issued_utc"])
            pv = {datetime.fromisoformat(k): float(v) for k, v in attrs["pv_kw_utc"].items()}
            use = {datetime.fromisoformat(k): float(v)
                   for k, v in attrs["use_surplus_kw_utc"].items()}
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        if (now - issued).total_seconds() > FORECAST_MAX_AGE_H * 3600:
            return None
        return pv, use

    def _solar(self):
        """Live surplus, forecast holdback and whether the forecast is trusted today."""
        now = datetime.now(timezone.utc)
        hour = now.replace(minute=0, second=0, microsecond=0)
        today = datetime.now().date()
        if self._distrust and self._distrust[0] != today:
            self.log("[SOLAR] new day, forecast trusted again")
            self._distrust = None
        for h in [h for h in self._pv_wh if h < now - timedelta(hours=18)]:
            del self._pv_wh[h]
            self._fc_kw.pop(h, None)

        grid = self._reading(GRID_SENSOR, GRID_STALE_S)
        pv = self._reading(PV_SENSOR, GRID_STALE_S)

        plan = self._read_plan(now)
        if plan:
            for h, kw in plan[0].items():
                if h > hour or h not in self._fc_kw:
                    self._fc_kw[h] = kw

        if pv is not None:
            acc = self._pv_wh.setdefault(hour, [0.0, 0])
            acc[0] += pv * CONTROL_INTERVAL / 3600
            acc[1] += 1
        self._check_forecast(now, hour, today)

        holdback = None
        if plan and not self._distrust:
            ahead = {h: kw for h, kw in plan[1].items()
                     if hour <= h < hour + timedelta(hours=HOLDBACK_HOURS)}
            if sum(ahead.values()) >= HOLDBACK_MIN_KWH:
                first = min(ahead)
                holdback = first.astimezone().strftime("%H") if first > hour else "nyt"

        if grid is not None and grid > SOLAR_IMPORT_ALLOW_W:
            self._import_cycles += 1
        else:
            self._import_cycles = 0

        live = grid is not None and (pv is None or pv >= SOLAR_MIN_PV_W)
        return {
            "live": live,
            "export_w": None if grid is None else -grid,
            "pv_w": pv,
            "holdback": holdback,
            "plan": plan is not None,
        }

    def _check_forecast(self, now, hour, today):
        """Distrust the forecast for the day if the inverter falls well short of it."""
        if self._distrust:
            return
        fc = act = 0.0
        for h, (wh, n) in self._pv_wh.items():
            if h < hour and n >= 45 and h in self._fc_kw:
                fc += self._fc_kw[h]
                act += wh * 60 / n / 1000
        if fc >= TRUST_MIN_FORECAST_KWH and act < TRUST_MIN_RATIO * fc:
            self._distrust = (today, f"tuotto {act:.1f} kWh, ennuste {fc:.1f} kWh")
        else:
            wh, n = self._pv_wh.get(hour, (0.0, 0))
            kw = self._fc_kw.get(hour, 0.0)
            if (now.minute >= 30 and n >= 20 and kw >= HOUR_CHECK_MIN_KW
                    and wh * 60 / n / 1000 < HOUR_CHECK_RATIO * kw):
                self._distrust = (today, f"tunnin tuotto {wh * 60 / n / 1000:.2f} kW, "
                                         f"ennuste {kw:.2f} kW")
        if self._distrust:
            self.log(f"[SOLAR] forecast distrusted for today: {self._distrust[1]}",
                     level="WARNING")

    def _admit_solar(self, sunny, solar, loads, rank):
        """Start rooms on surplus, warmest-room-last, as far as the export covers them."""
        budget = None
        if solar["live"] and solar["export_w"] is not None and loads is not None:
            budget = solar["export_w"] + SOLAR_IMPORT_ALLOW_W - getattr(self, "_started_w", 0)

        for gap, room, cfg, aim, temp, target, flex, note in sorted(
                sunny, key=lambda w: w[0], reverse=True):
            phase, watts = self.cap_watts(room)
            fits = budget is not None and watts <= budget and (
                all(l + watts <= PHASE_LIMIT_W for l in loads.values()) if phase is None
                else loads[phase] + watts <= PHASE_LIMIT_W)
            if fits:
                self.call_service("input_boolean/turn_on", entity_id=cfg["relay"])
                self._solar_rooms.add(room)
                budget -= watts
                for p in ([phase] if phase else loads):
                    loads[p] += watts
                self.log(f"[SOLAR] {room} on surplus ({watts:.0f} W, "
                         f"export {solar['export_w']:.0f} W)")
                reason = self._explain(rank, aim, temp, self._solar_note(target + flex))
            else:
                reason = self._explain(rank, aim, temp, note)
            self._publish_target(room, aim, target, flex, rank,
                                 temperature=temp, reason=reason)

    def _solar_stop(self, solar, rank):
        """Importing for SOLAR_STOP_AFTER cycles: drop one solar room, warmest first."""
        if self._import_cycles < SOLAR_STOP_AFTER:
            return
        on = [r for r in self._solar_rooms
              if self.get_state(LOOPS[r]["relay"]) == "on"]
        self._solar_rooms &= set(on)
        if not on:
            return

        def over(room):
            try:
                return float(self.get_state(LOOPS[room]["temp"])) - self._number(
                    LOOPS[room]["setpoint"], 21.0)
            except (TypeError, ValueError):
                return 99.0
        room = max(on, key=over)
        self.log(f"[SOLAR] importing {-solar['export_w']:.0f} W for "
                 f"{self._import_cycles} cycles: {room} off")
        self.call_service("input_boolean/turn_off", entity_id=LOOPS[room]["relay"])
        self._solar_rooms.discard(room)

    def _publish_solar(self, solar):
        if self._solar_rooms:
            state = "heating on solar surplus"
        elif solar["holdback"]:
            state = "waiting for sun"
        else:
            state = "no surplus"
        hours = [h for h in self._pv_wh if h in self._fc_kw and self._pv_wh[h][1] >= 45]
        self.set_state(SOLAR_STATUS, state=state, attributes={
            "friendly_name": "Heating and solar",
            "icon": "mdi:solar-power",
            "export_w": solar["export_w"],
            "pv_w": solar["pv_w"],
            "solar_rooms": sorted(self._solar_rooms),
            "holdback_from": solar["holdback"],
            "forecast_in_use": solar["plan"] and not self._distrust,
            "forecast_distrusted": None if not self._distrust else self._distrust[1],
            "forecast_kwh_today": round(sum(self._fc_kw[h] for h in hours), 2),
            "produced_kwh_today": round(sum(self._pv_wh[h][0] * 60 / self._pv_wh[h][1] / 1000
                                           for h in hours), 2),
        })

    def _publish_target(self, room, aim, target, flex, rank, **extra):
        """Publish sensor.<room>_target for every room, in every mode.

        This used to be written only for rooms the optimiser was actually
        driving. With 23 of 24 rooms on Manual that meant 23 cards reading
        "Entity not found" - a dashboard that looks broken rather than
        idle, which is the same "half a feature is worse than none" shape as
        shipping di_chaining with a fire-alarm rule that could not fire.

        aim is None when no target is being pursued, and the state stays
        `unknown` rather than falling back to the setpoint. A number here would
        draw a target line on the Miksi graph that nothing is tracking - the
        same reason dataset_logger writes blanks instead of zeros.
        """
        self.set_state(
            f"sensor.{room}_target",
            state="unknown" if aim is None else round(aim, 2),
            attributes={
                "friendly_name": f"{room} - momentary target",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement",
                "icon": "mdi:target",
                "asetus": target,
                "jousto": flex,
                "floor": round(target - flex, 2),
                "ceiling": round(target + flex, 2),
                "price_rank": None if rank is None else round(rank, 3),
                **extra,
            },
        )

    @staticmethod
    def _explain(rank, aim, temp, note=None):
        if note:
            return f"{note}; tavoite {aim:.1f} °C, nyt {temp:.1f} °C"
        if rank is None:
            return f"No price data - holding at the floor, {aim:.1f} C"
        if rank < 0.25:
            band = "cheap - storing heat in the building"
        elif rank < 0.6:
            band = "mid-priced"
        else:
            band = "expensive - coasting down to the floor"
        return f"{band}, target {aim:.1f} C, now {temp:.1f} C"

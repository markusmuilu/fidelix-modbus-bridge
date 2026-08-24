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
from datetime import datetime

from heating_rooms import LOOPS
from heating_mode import mode_of, OPTIMIZER

CONTROL_INTERVAL = 60

PRICE_SENSOR = "sensor.spot_prices"

# Slots ahead to rank against. A full day, so "cheap" means cheap relative to
# what is actually coming rather than to the last hour.
HORIZON_SLOTS = 96

# Deadband around the effective target, to stop the relay chattering.
DEADBAND = 0.3

DEFAULT_FLEX = 1.0


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
        acted = 0

        for room, cfg in LOOPS.items():
            mode = mode_of(self, room)
            target = self._number(cfg["setpoint"], 21.0)
            flex = self._number(flex_of(room), DEFAULT_FLEX)

            if mode != OPTIMIZER:
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
                self._publish_target(
                    room, None, target, flex, rank,
                    reason="Temperature unreadable - output left as it was",
                )
                continue

            aim = self.effective_target(target, flex, rank)

            if temp < aim - DEADBAND:
                self.call_service("input_boolean/turn_on", entity_id=cfg["relay"])
            elif temp > aim + DEADBAND:
                self.call_service("input_boolean/turn_off", entity_id=cfg["relay"])

            acted += 1
            self._publish_target(
                room, aim, target, flex, rank,
                temperature=temp,
                reason=self._explain(rank, aim, temp),
            )

        if acted:
            self.log(
                f"[SMART] {acted} room(s), price rank "
                f"{'unknown' if rank is None else f'{rank:.2f}'}"
            )

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
    def _explain(rank, aim, temp):
        if rank is None:
            return f"No price data - holding at the floor, {aim:.1f} C"
        if rank < 0.25:
            band = "cheap - storing heat in the building"
        elif rank < 0.6:
            band = "mid-priced"
        else:
            band = "expensive - coasting down to the floor"
        return f"{band}, target {aim:.1f} C, now {temp:.1f} C"

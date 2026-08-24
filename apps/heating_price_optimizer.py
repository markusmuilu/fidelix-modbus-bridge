import appdaemon.plugins.hass.hassapi as hass
from datetime import datetime
import os
import time

from heating_mode import mode_of, OPTIMIZER

os.environ["TZ"] = "Europe/Helsinki"   # site timezone
time.tzset()


CONTROL_INTERVAL = 60

HEATING_LOOPS = {
    "zone_01": "input_boolean.zone_01_heating",
    "zone_02": "input_boolean.zone_02_heating",
    "zone_03": "input_boolean.zone_03_heating",
    "zone_04": "input_boolean.zone_04_heating",
    "zone_05": "input_boolean.zone_05_heating",
    "zone_06": "input_boolean.zone_06_heating",
    "zone_07": "input_boolean.zone_07_heating",
    "zone_08": "input_boolean.zone_08_heating",
    "zone_09": "input_boolean.zone_09_heating",
    "zone_10": "input_boolean.zone_10_heating",
    "zone_11": "input_boolean.zone_11_heating",
    "zone_12": "input_boolean.zone_12_heating",
    "zone_13": "input_boolean.zone_13_heating",
    "zone_14": "input_boolean.zone_14_heating",
    "zone_15": "input_boolean.zone_15_heating",
    "zone_16": "input_boolean.zone_16_heating",
    "zone_17": "input_boolean.zone_17_heating",
    "zone_18": "input_boolean.zone_18_heating",
    "zone_19": "input_boolean.zone_19_heating",
    "zone_20": "input_boolean.zone_20_heating",
    "zone_21": "input_boolean.zone_21_heating",
    "zone_22": "input_boolean.zone_22_heating",
    "zone_23": "input_boolean.zone_23_heating",
    "zone_24": "input_boolean.zone_24_heating",
}

# Transfer tariff, EUR/kWh, added to the spot price before ranking - what you
# pay is spot plus transfer, and a tariff with a day/night structure can reorder
# which slots are actually cheapest. REDACTED: the real values are contract
# specific. Read them off your own bill.
DAY_TRANSFER = 0.05
NIGHT_TRANSFER = 0.03


class HeatingPriceOptimizer(hass.Hass):

    def initialize(self):
        self.allowed_slots = {}

        self.listen_state(self.recalculate, "sensor.spot_prices")
        for name in HEATING_LOOPS:
            self.listen_state(self.recalculate, f"input_boolean.{name}_price_optimization")
            self.listen_state(self.recalculate, f"input_number.{name}_heating_hours")

        self.run_every(self.enforce, self.datetime(), CONTROL_INTERVAL)
        self.recalculate(None)

    def slot_key(self, dt: datetime):
        minute = (dt.minute // 15) * 15
        return dt.replace(minute=minute, second=0, microsecond=0, tzinfo=None)


    def _fmt(self, slots):
        return [f"{t.strftime('%H:%M')}={p:.4f}" for t, p in slots]



    def recalculate(self, *args):
        sensor = self.get_state("sensor.spot_prices", attribute="all")
        if not sensor:
            self.log("No spot price data")
            return

        times = sensor["attributes"].get("times", [])
        prices = sensor["attributes"].get("prices", [])

        slots = []
        for t, p in zip(times, prices):
            try:
                ts = datetime.fromisoformat(t)
            except ValueError:
                continue

            key = self.slot_key(ts)
            total = p + self.transfer_price(ts)
            slots.append((key, total))

        slots_by_price = sorted(slots, key=lambda x: x[1])
        slots_by_time = sorted(slots, key=lambda x: x[0])

        for name in HEATING_LOOPS:
            hours = float(self.get_state(f"input_number.{name}_heating_hours") or 4)
            count = int(hours * 4)

            selected = slots_by_price[:count]
            self.allowed_slots[name] = {t for t, _ in selected}

            chrono = sorted(selected, key=lambda x: x[0])
            by_price = sorted(selected, key=lambda x: x[1])

            self.log(f"Now: {datetime.now()}")
            self.log(f"[{name}] wants {hours}h -> {count} slots")
            self.log(f"[{name}] chronological: {self._fmt(chrono)}")
            self.log(f"[{name}] by price:      {self._fmt(by_price)}")


    def enforce(self, kwargs):
        now_key = self.slot_key(datetime.now())

        for name, relay in HEATING_LOOPS.items():
            # Was: if the thermostat was also enabled, this called turn_off on
            # input_boolean.<name> to win the fight - silently undoing a switch
            # the user had just set, with nothing in the UI to explain it. That
            # was the concrete bug behind the owner's "ettei tarvis miettia onko joku
            # toinen ohjaus joka yliohjaa". Modes are exclusive now, so there is
            # no fight to win and nothing to override.
            if mode_of(self, name) != OPTIMIZER:
                continue

            allowed = now_key in self.allowed_slots.get(name, set())

            self.log(f"[{name}] hour={now_key} allowed={allowed}")

            self.call_service(
                "input_boolean/turn_on" if allowed else "input_boolean/turn_off",
                entity_id=relay,
            )

    def transfer_price(self, ts: datetime) -> float:
        hour = ts.hour
        wd = ts.weekday()
        m = ts.month

        winter = (m >= 11 or m <= 3)

        if winter:
            if wd <= 5 and 7 <= hour < 22:
                return DAY_TRANSFER
            else:
                return NIGHT_TRANSFER
        else:
            return NIGHT_TRANSFER
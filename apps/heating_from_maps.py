import appdaemon.plugins.hass.hassapi as hass

from heating_mode import mode_of, THERMOSTAT
from heating_rooms import LOOPS, HYSTERESIS, CONTROL_INTERVAL

class HeatingFromMaps(hass.Hass):

    def initialize(self):
        self.log("=== HeatingFromMaps started ===")
        self.run_every(self.control, self.datetime(), CONTROL_INTERVAL)

    def control(self, kwargs):
        for name, cfg in LOOPS.items():
            # The mode is the authority now, not cfg["enabled"] - see
            # heating_mode.py. That boolean is kept as a mirror for the existing
            # dashboards, so reading it here would still work today, but it
            # would quietly reintroduce two sources of truth.
            if mode_of(self, name) != THERMOSTAT:
                continue

            try:
                temp = float(self.get_state(cfg["temp"]))
                sp = float(self.get_state(cfg["setpoint"]))
            except (TypeError, ValueError):
                continue

            if temp < sp - HYSTERESIS:
                self.call_service("input_boolean/turn_on", entity_id=cfg["relay"])
            elif temp > sp + HYSTERESIS:
                self.call_service("input_boolean/turn_off", entity_id=cfg["relay"])

"""The room map, in one place.

Extracted from heating_from_maps.py so heating_mode.py can use it without an
import cycle - heating_from_maps needs the mode, and the mode needs the rooms.

Every app that touches per-room heating imports these rather than keeping its
own copy, so a room cannot exist in one map and not another.
"""

HYSTERESIS = 0.5
CONTROL_INTERVAL = 30

LOOPS = {
    "zone_01": {
        "temp": "sensor.zone_01_temperature",
        "setpoint": "input_number.zone_01_setpoint",
        "enabled": "input_boolean.zone_01",
        "relay": "input_boolean.zone_01_heating",
    },
    "zone_02": {
        "temp": "sensor.zone_02_temperature",
        "setpoint": "input_number.zone_02_setpoint",
        "enabled": "input_boolean.zone_02",
        "relay": "input_boolean.zone_02_heating",
    },
    "zone_03": {
        "temp": "sensor.zone_03_temperature",
        "setpoint": "input_number.zone_03_setpoint",
        "enabled": "input_boolean.zone_03",
        "relay": "input_boolean.zone_03_heating",
    },
    "zone_04": {
        "temp": "sensor.zone_04_temperature",
        "setpoint": "input_number.zone_04_setpoint",
        "enabled": "input_boolean.zone_04",
        "relay": "input_boolean.zone_04_heating",
    },
    "zone_05": {
        "temp": "sensor.zone_05_temperature",
        "setpoint": "input_number.zone_05_setpoint",
        "enabled": "input_boolean.zone_05",
        "relay": "input_boolean.zone_05_heating",
    },

    "zone_06": {
        "temp": "sensor.zone_06_ad",
        "setpoint": "input_number.zone_06_setpoint",
        "enabled": "input_boolean.zone_06",
        "relay": "input_boolean.zone_06_heating",
    },
    "zone_07": {
        "temp": "sensor.zone_07_ad",
        "setpoint": "input_number.zone_07_setpoint",
        "enabled": "input_boolean.zone_07",
        "relay": "input_boolean.zone_07_heating",
    },

    "zone_08": {
        "temp": "sensor.zone_08_ad",
        "setpoint": "input_number.zone_08_setpoint",
        "enabled": "input_boolean.zone_08",
        "relay": "input_boolean.zone_08_heating",
    },
    "zone_09": {
        "temp": "sensor.zone_09_ad",
        "setpoint": "input_number.zone_09_setpoint",
        "enabled": "input_boolean.zone_09",
        "relay": "input_boolean.zone_09_heating",
    },
    "zone_10": {
        "temp": "sensor.zone_10_ad",
        "setpoint": "input_number.zone_10_setpoint",
        "enabled": "input_boolean.zone_10",
        "relay": "input_boolean.zone_10_heating",
    },

    "zone_11": {
        "temp": "sensor.zone_11_ad",
        "setpoint": "input_number.zone_11_setpoint",
        "enabled": "input_boolean.zone_11",
        "relay": "input_boolean.zone_11_heating",
    },
    "zone_12": {
        "temp": "sensor.zone_12_ad",
        "setpoint": "input_number.zone_12_setpoint",
        "enabled": "input_boolean.zone_12",
        "relay": "input_boolean.zone_12_heating",
    },

    "zone_13": {
        "temp": "sensor.zone_13_katto_ad",
        "setpoint": "input_number.zone_13_setpoint",
        "enabled": "input_boolean.zone_13",
        "relay": "input_boolean.zone_13_heating",
    },
    "zone_14": {
        "temp": "sensor.zone_14_lattia_ad",
        "setpoint": "input_number.zone_14_setpoint",
        "enabled": "input_boolean.zone_14",
        "relay": "input_boolean.zone_14_heating",
    },
    "zone_15": {
        "temp": "sensor.zone_15_lattia_ad",
        "setpoint": "input_number.zone_15_setpoint",
        "enabled": "input_boolean.zone_15",
        "relay": "input_boolean.zone_15_heating",
    },
    "zone_16": {
        "temp": "sensor.zone_16_lattia_ad",
        "setpoint": "input_number.zone_16_setpoint",
        "enabled": "input_boolean.zone_16",
        "relay": "input_boolean.zone_16_heating",
    },
    "zone_17": {
        "temp": "sensor.zone_17_lattia_ad",
        "setpoint": "input_number.zone_17_setpoint",
        "enabled": "input_boolean.zone_17",
        "relay": "input_boolean.zone_17_heating",
    },
    "zone_18": {
        "temp": "sensor.zone_18_lattia_ad",
        "setpoint": "input_number.zone_18_setpoint",
        "enabled": "input_boolean.zone_18",
        "relay": "input_boolean.zone_18_heating",
    },
    "zone_19": {
        "temp": "sensor.zone_19_ad",
        "setpoint": "input_number.zone_19_setpoint",
        "enabled": "input_boolean.zone_19",
        "relay": "input_boolean.zone_19_heating",
    },

    "zone_20": {
        "temp": "sensor.zone_20_ad",
        "setpoint": "input_number.zone_20_setpoint",
        "enabled": "input_boolean.zone_20",
        "relay": "input_boolean.zone_20_heating",
    },
    "zone_21": {
        "temp": "sensor.zone_21_ad",
        "setpoint": "input_number.zone_21_setpoint",
        "enabled": "input_boolean.zone_21",
        "relay": "input_boolean.zone_21_heating",
    },
    "zone_22": {
        "temp": "sensor.zone_22_ad",
        "setpoint": "input_number.zone_22_setpoint",
        "enabled": "input_boolean.zone_22",
        "relay": "input_boolean.zone_22_heating",
    },
    "zone_23": {
        "temp": "sensor.zone_23_ad",
        "setpoint": "input_number.zone_23_setpoint",
        "enabled": "input_boolean.zone_23",
        "relay": "input_boolean.zone_23_heating",
    },
    "zone_24": {
        "temp": "sensor.zone_24_ad",
        "setpoint": "input_number.zone_24_setpoint",
        "enabled": "input_boolean.zone_24",
        "relay": "input_boolean.zone_24_heating",
    },
}


# Element rating and phase per room, in watts.
#
# Four notes have listed cost-per-room as blocked on "unknown element ratings".
# They were never unknown - they are in the relay entities' friendly names in
# the HA UI ("Zone 01 700W L3"), which carry both the wattage and the
# phase. Read off a screenshot of the entity list on 2026-08-03; the remaining
# 19 need the same treatment.
#
# A room absent from this map has an UNKNOWN rating, not a zero one. Nothing
# here may default to a number: hours x watts is the only step between runtime
# and euros, so an invented rating would produce an authoritative-looking cost
# for a room nobody measured. Same rule as dataset_logger writing blanks rather
# than zeros. Consumers must use element_of() and skip rooms it returns None
# for, rather than treating this dict as complete.
ELEMENTS = {
    "zone_01": {"watts": 700, "phase": 3},
    "zone_02": {"watts": 1050, "phase": 3},
    "zone_03": {"watts": 970, "phase": 1},
    "zone_04": {"watts": 900, "phase": 1},
    "zone_05": {"watts": 1200, "phase": 3},
}


def element_of(room):
    """Rating and phase for a room, or None if it has not been read off yet."""
    return ELEMENTS.get(room)


def rated_rooms():
    """The rooms a kWh or euro figure may legitimately be computed for."""
    return [room for room in LOOPS if room in ELEMENTS]

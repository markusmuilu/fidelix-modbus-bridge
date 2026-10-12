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
# The first five are the labels in the relay entities' friendly names in the HA
# UI ("Zone 01 700W L3"). The rest are from an element census: one relay at a
# time, per-phase delta from the grid meter. The census matched all five labels
# within 8 %, same phase.
#
# "source" says how much to trust each number:
#   label     - printed rating
#   measured  - 2+ clean census readings that agree
#   single    - one clean census reading
#   estimate  - no clean reading; the window overlapped other loads
#
# A room absent from this map has an UNKNOWN rating, not a zero one. Two
# circuits drew no power in the census (zone_22 and zone_23: local
# thermostat, breaker or dead element?), so they are absent. Nothing here may
# default to a number: hours x watts is the only step between runtime and euros,
# so an invented rating would produce an authoritative-looking cost for a room
# nobody measured. Consumers must use element_of() and handle None.
#
# Two zones (zone_09 and zone_15) were crossed in the original labelling; the
# census measured them under the old names, so their two figures are swapped
# here to follow the hardware.
#
# If an electrician moves a circuit to another phase, change "phase" here. The
# per-phase cap in heating_smart reads it from nowhere else.
ELEMENTS = {
    "zone_01": {"watts": 700, "phase": 3, "source": "label"},
    "zone_02": {"watts": 1050, "phase": 3, "source": "label"},
    "zone_03": {"watts": 970, "phase": 1, "source": "label"},
    "zone_04": {"watts": 900, "phase": 1, "source": "label"},
    "zone_05": {"watts": 1200, "phase": 3, "source": "label"},
    "zone_06": {"watts": 2138, "phase": 1, "source": "measured"},
    "zone_07": {"watts": 1077, "phase": 2, "source": "estimate"},
    "zone_08": {"watts": 440, "phase": 2, "source": "estimate"},
    "zone_09": {"watts": 947, "phase": 1, "source": "estimate"},
    "zone_10": {"watts": 444, "phase": 3, "source": "measured"},
    "zone_11": {"watts": 867, "phase": 1, "source": "single"},
    "zone_12": {"watts": 1520, "phase": 3, "source": "single"},
    "zone_13": {"watts": 520, "phase": 3, "source": "measured"},
    "zone_14": {"watts": 612, "phase": 2, "source": "measured"},
    "zone_15": {"watts": 222, "phase": 1, "source": "single"},
    "zone_16": {"watts": 1468, "phase": 1, "source": "estimate"},
    "zone_17": {"watts": 609, "phase": 2, "source": "estimate"},
    "zone_18": {"watts": 459, "phase": 3, "source": "measured"},
    "zone_19": {"watts": 663, "phase": 2, "source": "measured"},
    "zone_20": {"watts": 475, "phase": 2, "source": "single"},
    "zone_21": {"watts": 253, "phase": 2, "source": "single"},
    "zone_24": {"watts": 1015, "phase": 1, "source": "measured"},
}


def element_of(room):
    """Rating and phase for a room, or None if it has not been read off yet."""
    return ELEMENTS.get(room)


def rated_rooms():
    """The rooms a kWh or euro figure may legitimately be computed for.

    Estimates are good enough to keep a phase under its limit with a margin,
    not to bill a room.
    """
    return [room for room in LOOPS
            if room in ELEMENTS and ELEMENTS[room]["source"] != "estimate"]

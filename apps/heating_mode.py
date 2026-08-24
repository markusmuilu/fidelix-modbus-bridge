"""Owns the answer to "which control drives this output".

Backlog item 6, the owner's wording:

    "Olisi hyva etta vois jostakin valita, etta mita lammityksen ohjausta DO
     lahto toteuttaa ettei tarvis miettia onko joku toinen ohjaus joka yliohjaa."

The verb is *valita* - choose. heating_ownership.py already shows who owns an
output; this is what lets you pick.

WHAT IT REPLACES
Before this, precedence was implicit and enforced by force. Two booleans decided
everything - input_boolean.<room> for the thermostat and
input_boolean.<room>_price_optimization for the optimiser - and if both were on,
heating_price_optimizer.enforce() called turn_off on the thermostat's boolean.
A switch the user had just set would flip back within 60 s with nothing in the
UI explaining why. That is exactly the guesswork the owner described.

Now input_select.<room>_mode is the single authoritative choice, and each
writer acts only when its own mode is selected. Nothing overrides anything.

BACKWARDS COMPATIBILITY
The old booleans are still all over the existing dashboards, so they stay - as
mirrors. This app keeps them in step with the select, and translates a toggle of
an old boolean into the corresponding mode change. Rules, in order:

  - the select is the truth
  - toggling an old boolean is a request, translated once and then re-mirrored
  - on first run, the select is derived from whatever the booleans already say,
    so switching to this app changes no behaviour on day one

Read the modes with mode_of(); the other apps import that rather than
duplicating the entity naming.
"""
import appdaemon.plugins.hass.hassapi as hass

from heating_rooms import LOOPS

MANUAL = "Manual"
THERMOSTAT = "Thermostat"
OPTIMIZER = "PriceOptimised"
SOLAR = "Solar"

MODES = [MANUAL, THERMOSTAT, OPTIMIZER, SOLAR]

SYNC_INTERVAL = 30


def select_of(room):
    return f"input_select.{room}_mode"


def thermostat_boolean_of(room):
    return f"input_boolean.{room}"


def optimizer_boolean_of(room):
    return f"input_boolean.{room}_price_optimization"


def mode_of(app, room):
    """The selected mode for a room, falling back to the legacy booleans.

    The fallback matters during the first minutes after deploy, before the
    input_selects exist in HA - and it reproduces the old precedence exactly,
    so behaviour degrades to what it was rather than to nothing.
    """
    state = app.get_state(select_of(room))
    if state in MODES:
        return state

    if app.get_state(optimizer_boolean_of(room)) == "on":
        return OPTIMIZER
    if app.get_state(thermostat_boolean_of(room)) == "on":
        return THERMOSTAT
    return MANUAL


class HeatingMode(hass.Hass):

    def initialize(self):
        self.log("=== HeatingMode started ===")
        self._applying = set()

        self.run_in(self._migrate, 3)

        for room in LOOPS:
            self.listen_state(self._select_changed, select_of(room), room=room)
            self.listen_state(self._legacy_changed, thermostat_boolean_of(room),
                              room=room, mode=THERMOSTAT)
            self.listen_state(self._legacy_changed, optimizer_boolean_of(room),
                              room=room, mode=OPTIMIZER)

        # Belt and braces: a periodic re-mirror, so a missed event or a restart
        # can't leave the booleans disagreeing with the select forever.
        self.run_every(self._resync, self.datetime(), SYNC_INTERVAL)

    # --------------------------------------------------

    def _migrate(self, kwargs):
        """Seed each select from the legacy booleans, once, without changing behaviour."""
        for room in LOOPS:
            current = self.get_state(select_of(room))
            if current in MODES and current != MANUAL:
                continue          # already chosen deliberately, leave it

            derived = MANUAL
            if self.get_state(optimizer_boolean_of(room)) == "on":
                derived = OPTIMIZER
            elif self.get_state(thermostat_boolean_of(room)) == "on":
                derived = THERMOSTAT

            if derived != current:
                self.log(f"[MODE] {room}: migrating -> {derived}")
                self._select(room, derived)

        self._resync(None)

    def _select(self, room, mode):
        self._applying.add(room)
        try:
            self.call_service(
                "input_select/select_option",
                entity_id=select_of(room),
                option=mode,
            )
        finally:
            self._applying.discard(room)

    # --------------------------------------------------

    def _select_changed(self, entity, attribute, old, new, kwargs):
        if old == new or new not in MODES:
            return
        room = kwargs["room"]
        self.log(f"[MODE] {room}: {old} -> {new}")
        self._mirror(room, new)

    def _legacy_changed(self, entity, attribute, old, new, kwargs):
        """An old dashboard toggle is a request to change mode."""
        room = kwargs["room"]
        if room in self._applying or old == new or new not in ("on", "off"):
            return

        wanted = kwargs["mode"] if new == "on" else MANUAL

        # Turning off a boolean that isn't the active mode means nothing - the
        # mirror already had it off, and we must not knock the room to manual.
        if new == "off" and mode_of(self, room) != kwargs["mode"]:
            return

        if mode_of(self, room) == wanted:
            return

        self.log(f"[MODE] {room}: legacy {entity} {old}->{new} => {wanted}")
        self._select(room, wanted)
        self._mirror(room, wanted)

    # --------------------------------------------------

    def _mirror(self, room, mode):
        """Drive the legacy booleans from the mode, so old cards stay honest."""
        wanted = {
            thermostat_boolean_of(room): mode == THERMOSTAT,
            optimizer_boolean_of(room): mode == OPTIMIZER,
        }

        self._applying.add(room)
        try:
            for entity, should_be_on in wanted.items():
                is_on = self.get_state(entity) == "on"
                if is_on != should_be_on:
                    self.call_service(
                        "input_boolean/turn_on" if should_be_on else "input_boolean/turn_off",
                        entity_id=entity,
                    )
        finally:
            self._applying.discard(room)

    def _resync(self, kwargs):
        for room in LOOPS:
            self._mirror(room, mode_of(self, room))

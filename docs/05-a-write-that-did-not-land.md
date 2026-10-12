# A write that did not land

## What happened

One zone's floor was heated for three days while Home Assistant showed its relay as off.
Later the same zone sat cold for days while Home Assistant showed it as on. The temperature
sensor was fine and the bus looked fine. The relay just was not doing what the dashboard said.

## Why

The bridge writes a whole 8-bit register at once, one bit per relay. A write that was skipped
because the bus was busy, or that failed on the wire, was logged at most and never repeated,
and Home Assistant kept showing the state it had asked for. The hardware only caught up when
some other output on the same module changed, which forced another write of the register.

The busy bus had a cause. A digital-input probe was using a function code the card does not
support. Each failed attempt held the bus for about five seconds every thirty seconds, which
is the same length as the bridge's own wait for the bus, so writes were skipped.

## What changed

- A write that does not land is retried every ten seconds until it does. The retry recomputes
  the value from the bits at that moment, so it carries the latest intent and not a stale one.
- Every minute each full-register module is read back and compared with Home Assistant. A
  module that differs is rewritten. Intent is re-read from Home Assistant at that point, so a
  state change the bridge missed is corrected as well.
- The read-back publishes a sensor with the number of corrections, and the watchdog treats a
  stale read-back as a fault.

`tests/test_output_writes.py` covers the cases without hardware: a busy bus, an exception, an
error response, never more than one retry pending per module, a retry that carries newer
intent, drift found by the read-back, a state change the bridge missed, and an unknown state
keeping the last known intent.

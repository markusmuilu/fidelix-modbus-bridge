# Fidelix FX-2020 over Modbus RTU: field notes

What was learned by reading registers on a live FX-2020 installation, written down
because almost none of it is available anywhere else. Fidelix is real,
widely-installed building automation kit with essentially no open-source Home
Assistant support, and the vendor documentation that exists is aimed at people
configuring the controller, not at people talking to it.

**Treat all of this as observed behaviour on one installation, not as a
specification.** Verify against your own cabinet before writing to anything.

## Transport

- Modbus RTU over RS-485, reached through a USB serial adapter.
- 9600 baud, 8 data bits, no parity, 1 stop bit, in the installation this was
  written against. Do not assume it; the controller is configurable and an
  installer set this decades ago.
- One request timeout of about a second is comfortable. Cards answer in roughly a
  hundred milliseconds when they answer at all.
- Each I/O card is a separate Modbus unit id. There is no single "controller"
  address that fans out.

Use a stable device path (`/dev/serial/by-id/...` or a udev rule). An adapter
enumerated as `/dev/ttyUSB0` will become `/dev/ttyUSB1` after a reboot with a
second adapter present, and you will then be polling something else entirely.

## Analogue input cards

Eight resistive channels, one holding register each, read from the card's base
address. The register is a raw 16-bit reading from a divider against an internal
pull-up, not a scaled temperature.

```
R = pullup * raw / (65535 - raw)
```

with `pullup` = 4700 Ω on the cards seen here. Convert resistance to temperature
against the thermistor's own R/T table - see [`resistance_to_temperature()`](../apps/modbus_bridge.py)
for why a table rather than a Beta fit, and why out-of-range must be a fault
rather than a clamped endpoint.

**A channel with nothing wired to it reads `0xFFFF`.** This is genuinely useful:
if you have a card and no reliable record of which channel is which, publish all
eight under positional names and let the data identify itself. The channels
returning a plausible resistance are the ones with sensors on them.

## Digital output cards

Eight relays packed as bits into **one** holding register at the card's base
address, lowest bit first. Read and written as a normal holding register - there
is no coil interface in play here.

Two things follow, and both matter more than they look:

**You cannot write one relay.** The unit of writing is the register, so touching
one bit means deciding what to say about all eight. Everything in
[`_write_shared_register()`](../apps/modbus_bridge.py) follows from this.

**The register reads back true hardware state.** This was the single most
load-bearing unverified assumption in the project for weeks - if output registers
had simply echoed the last value written, read-modify-write would silently degrade
into the full-register write it exists to avoid, and every guarantee built on top
of it would be decoration.

It was settled by accident, and cheaply. Every earlier read had returned all
zeros, which distinguishes nothing: "the plant is idle" and "reads are
meaningless" look identical. Then one startup adoption returned a single bit high
with the other seven low. One bit high among seven low can only mean the register
reflects reality.

If you are integrating a Fidelix installation, get this evidence early and get it
read-only. A plant item that runs intermittently on the controller's own schedule
will give it to you for free if you just log the register for a day.

## Digital input cards

**Sixteen points per card, where analogue and output cards carry eight.** Which
means you cannot assume the register layout matches, and there are at least three
plausible encodings:

- one holding register of 16 bits
- two holding registers of 8 bits each
- discrete inputs

No documentation encountered says which. Reading cannot actuate anything, so the
safe approach is to probe: read all three encodings, log what each returns, and
let a human pick from real data rather than guessing. That is what "probe mode"
means in this codebase.

Two findings from doing that, offered as warnings rather than facts about your
site:

- **Polarity was inverted** - a `1` meant *not running*. Assume nothing.
- **Points 1-8 read identically under two of the three candidate encodings**, so
  an experiment that only exercises those points discriminates nothing. If you are
  designing a probe, make sure the point you toggle falls in the range where the
  encodings actually differ. This cost a wasted experiment.

## Cards that are documented but not fitted

Commissioning paperwork describes the design. The cabinet contains the
installation, and they are not the same document. One unit id present in the
point list simply did not exist on the bus, and every poll spent three stacked
timeouts finding that out again.

Give up on a unit that has failed a couple of times in a row, say so once, and
reset the counter if it ever answers - a card that was merely powered down should
come back on its own. See the give-up counter in the DI reader.

## Sharing the bus with the controller's own program

The FX-2020 continues running its own control program. On any card where that
program drives points, expect it to fight you or overwrite you, and design for
that rather than assuming you have exclusive control of anything.

This is a second, subtler reason to prefer read-modify-write everywhere: it makes
your writes minimal and your reads authoritative, which is the only posture that
works when you are not the only thing writing.

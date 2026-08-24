# A running system is not a verified system

*Five faults in one afternoon, in something that had been "working fine" for
months.*

## What happened

The system had been running unattended for the better part of a year. Nobody had
complained. The house was warm. On the dashboard, everything read plausibly.

Sitting down to read the code properly - not to fix anything, just to understand
it before adding a feature - turned up five separate faults in a single afternoon.
Every one of them had been failing silently. Every one of them had been failing
for days or months. None of them had ever produced an error, a notification, or a
visibly wrong number.

**1. The price feed had been pointing at an entity that no longer existed.** The
electricity-price integration had renamed its sensor at some upgrade; a template
sensor still referenced the old name. Referencing a missing entity is not an error
in Home Assistant - it renders as unavailable. The price-optimisation layer had
therefore been optimising against nothing at all, and had been for an unknown
number of weeks. The heating still worked, because the plain thermostat underneath
it still worked. The entire feature the system existed for had simply stopped, and
the system's answer to "is everything running?" was still yes.

**2. The bus had been wedged for two days.** Its own story, in
[01-two-masters-on-one-bus.md](01-two-masters-on-one-bus.md).

**3. The electricity transfer tariff was wrong for the season.** A hard-coded
summer branch that did not match the actual contract. This one had produced
subtly wrong prices for months - not absent, not zero, just wrong by a consistent
amount, which is the single hardest kind of error to notice from a graph.

**4. A dashboard chart read the same dead entity as fault 1.** Found only because
fixing fault 1 meant grepping for the old name.

**5. The temperature sensors had never been recording anything.** The worst of the
five, and the one with no fix that recovers anything. The sensors were being
published without a `state_class` attribute, so the recorder computed no long-term
statistics for them, and there was no retention policy overriding the ten-day
default purge. Months of thirty-second readings from two dozen sensors had been
taken, displayed, and thrown away. When it came time to fit a thermal model to
historical data, there was no historical data. There never had been.

## The pattern

Line them up and they are the same fault five times.

Every one is a **silent** failure. Not one produced an exception, a log line
anybody would see, or a value that looked wrong. A missing entity renders as
blank. A stale reading renders as a number. A wrong tariff renders as a number. A
discarded history renders as an empty graph that looks like a new install.

Every one was found by **a person reading the source**, not by the system. There
was no mechanism anywhere in it whose job was to notice that a part of it had gone
quiet. It had a dashboard, which answers *what is the value* - and nothing at all
answering *is this value still being produced*.

And every one had been running that way for a long time. "It's been fine for
months" was true and told you nothing, because nothing was checking.

## What was built in response

A watchdog, and it is deliberately stupid. It checks **ages, not values**.

```
watched:
  bus_last_read:   5 minutes
  price_feed:      6 hours
  pv_production:   30 minutes
  grid_meter:      15 minutes
```

For each, one question: when did this last change? Not "is this number sensible" -
sensibility checks require a model of what sensible means, and every one of the
five faults above produced a perfectly sensible-looking number. Age is the failure
mode that actually occurs, repeatedly, and it needs no model.

Thresholds are generous multiples of each source's real cadence. The bus polls
every thirty seconds and the threshold is five minutes, so it takes roughly ten
consecutive misses to fire. The point is catching *stopped entirely*, not jitter.

Two design details worth stealing:

**A separate heartbeat, distinct from the data.** The bridge publishes a sensor
whose only value is a timestamp of when the bus was last successfully read. Sensor
staleness alone is not a reliable liveness signal, because a wedged poll leaves the
last readings sitting there looking perfectly plausible - that is precisely how the
two-day outage hid. The heartbeat says *when we last looked*, separately from *what
we saw*.

**Optional sources may be absent without crying wolf.** A solar inverter that is
not installed is not a fault. Sources are declared optional or required, so an
install without the optional half does not produce a permanent alarm nobody can
clear - and an alarm nobody can clear is an alarm everybody learns to ignore,
which puts you back where you started.

## What generalises

- **"It's been running for months" is a statement about uptime, not
  correctness.** They are unrelated properties, and only one of them is usually
  measured.
- **Add the thing that notices before you add the next feature.** Every one of
  these faults was cheap to fix and impossible to see. The watchdog was two hours'
  work and would have caught four of the five on the day they started.
- **Monitor ages, not values, when you can only afford to monitor one.** It needs
  no domain model, it has no false-positive tuning problem, and it catches the
  failure shape that actually happens: something stopped and nobody noticed.
- **Silent degradation is the default behaviour of loosely-coupled systems.**
  Referencing something that does not exist is not an error in most
  configuration-driven platforms - it is a blank. Every integration boundary is a
  place where a rename becomes a silent no-op.
- **If you are going to want the data later, check today that it is being kept.**
  Not that it is being *collected* - collected and retained are different
  questions, and the gap between them was months of readings.

# Two masters on one bus

*Or: a system that has been replaced but not torn down is worse than one still
running.*

## The symptom

Temperatures on the dashboard were correct. They had been correct all week. They
were also, it turned out, forty-eight hours old.

Nothing had alerted. Nothing had errored. The graphs were flat, but a flat line
in a heating system in summer is not obviously wrong - rooms sitting still at
twenty-something degrees is exactly what a working system looks like when nobody
is asking it for anything. The failure was found because somebody sat down to
read the code for an unrelated reason and noticed a timestamp.

Underneath, the bridge's polling thread had been blocked inside a single Modbus
read since a Wednesday afternoon. That thread also carried every write, because
the scheduler runs callbacks on a shared pool and this app's callbacks all landed
on the same one. So for two days the system had not read a single temperature and
could not have switched a single relay. It looked, in every way a person could
see, like it was working.

## The cause

Modbus RTU over a serial line has exactly one master. Everyone knows this. It is
the first line of every introduction to the protocol.

The installation had two.

The bridge described in this repository replaced an earlier flow-based automation
that did the same job. "Replaced" turned out to mean *the new one was written and
deployed*. The old one was still installed, still enabled, still starting on boot,
and still polling the same serial port every hundred milliseconds - writing to the
same output cards, from stale hard-coded logic nobody had looked at in months.

Two masters on a shared RTU pair do not take turns. They interleave frames. Most
of the time both sides get a checksum error, retry, and muddle through; the reads
come back slower and mostly right, which is why this had been survivable for
months rather than fatal on day one. Then one read does not come back at all, the
call blocks past its own timeout, and the thread is gone.

The second system was not merely redundant. It was *invisible*. Nobody was looking
at its logs, because in everyone's mental model it had been decommissioned. It had
also retained the ability to switch real heating relays that entire time, on rules
that predated every decision made since.

## The fix, in three parts

**Delete the losing system, not just its schedule.** The old flows were removed
outright rather than disabled. A disabled system is one checkbox from being an
active one, and the checkbox will get clicked by somebody who does not know why it
was unchecked.

**One client, process-wide.** The bridge holds a module-level lock and hands out a
connected client through the `bus()` context manager. Every bus access in the process
goes through it. Nothing else opens a port.

**Acquire with a timeout, and yield nothing rather than block.** A caller that
cannot get the bus within five seconds is handed `None` and skips this cycle:

```python
with bus.acquire("temperature poll") as client:
    if client is None:
        return
    ...
```

A skipped read is a gap in a graph. A blocked callback is a pinned thread, and a
pinned thread in a shared pool takes actuation down with it. Given that choice,
drop the read every time.

## Then it happened again, from inside

Two days later a second app was added to read the input cards. Its docstring said,
correctly and at some length, that it must never open its own client - the whole
point of the incident above. It called the bridge's `_client()` helper, expecting
to be handed the shared one.

`_client()` was a factory. It returned a new one.

The serial library takes an exclusive OS lock on the port, so this failed
differently and more honestly than the original: the bridge could not open the
port at all, and said so, and every output write failed for as long as the input
reader was polling. Caught within minutes because the failure was loud. But it was
the same defect - two masters - shipped by the app whose entire justification was
preventing it, written by someone who had spent two days on the first one.

That is why `bus.py` exposes no way to get a client except through `acquire()`,
and why the docstring says what it says. A helper that returns a fresh client is a
second master with extra steps. The naming has to make the wrong thing hard, not
just the documentation.

## And then the granularity was wrong too

The first version of the shared bus held the lock for a whole polling sweep across
every input card. That is correct when every card answers in about a hundred
milliseconds. It is badly wrong when one card does not answer at all.

One unit id in the commissioning documents was never actually fitted. Three
stacked request timeouts against it blocked the bus for eighteen seconds - well
past the five-second acquire timeout every other caller uses - so a read-only poll
was starving actuation while it waited for a card that was never going to reply,
every cycle, forever.

Two changes: take the bus for one transaction rather than one sweep, and stop
polling a unit that has failed twice in a row. A module in a
design document is not a module in a cabinet. Not everything drawn gets installed.

## What generalises

- **A replaced system that is still running is worse than one you never
  replaced.** It now competes with its successor, and it does so invisibly,
  because everyone has stopped looking at it.
- **Shared exclusive resources need one owner in code, not one owner by
  convention.** Convention is what the second incident was: everyone agreed there
  should be one client, and the API let you get a second one by accident.
- **Prefer failures that are loud over failures that are silent**, even when the
  loud one is more disruptive. The exclusive port lock produced a two-minute
  outage that was diagnosed immediately. The frame collisions produced a two-day
  outage that looked like a working system.
- **Time out and give up.** Any blocking call on a shared resource, from a
  callback running on a shared pool, is a thread you are betting you will get
  back.

# When software can open a door

*A design question about a safety-critical output, the answer, and the fact that
the first answer was wrong.*

## The situation

Building automation cabinets are not laid out for the convenience of whoever
integrates them thirty years later. An output card is eight relays in one Modbus
register, and what is wired to those eight relays was decided by whoever
commissioned the building, on the basis of which wires reached which terminal.

So one card in this installation carried, in the same sixteen bits: a couple of
ordinary domestic loads, and a handful of points belonging to the building's
original security system - the sort of points that, in a building where they are
wired, move a lock or sound an alarm.

There is no version of this where those points are in a different register. They
share a register with the loads because they share a card with the loads. Any code
that writes one writes the other.

## Fault one: the full-register write

The obvious way to drive eight relays from eight switches is to compose the byte
and write it:

```python
value = 0
for bit, entity in enumerate(points):
    if state(entity) == "on":
        value |= (1 << bit)
client.write_register(address, value, unit)
```

This is correct, and it is what `write_owned_register` still does - for cards where
every bit is yours.

On a shared card it is a disaster, and not a subtle one. It does not leave the
other bits alone. It *explicitly re-asserts zero* on every bit you did not
account for, every time anything you do own changes. Toggle a light and you have
just written zero to seven other relays.

The fix is ordinary: read the live register, change only your bits, write it back.
That is `write_shared_register`, and about half of this repository exists to make
that operation safe.

## Fault two: the card nobody was worried about

Here is the part that is genuinely uncomfortable.

Everyone involved was careful about the security card. It got the read-modify-write
path, it got review, it got a test suite. Meanwhile a second card - plant
equipment: ventilation, a circulation pump, a valve - was declared as **owned
outright**, because on paper all eight of its points belonged to this controller.

It did. And that was still wrong, for a reason that has nothing to do with
ownership.

The startup path for an owned card asserts the controller's state onto the
hardware. Those eight switches had been created minutes earlier, and a freshly
created switch defaults to off. The scheduler hot-reloads on file change. So the
sequence was:

1. someone runs `git pull` on the box
2. the scheduler reloads the app
3. startup composes the register from eight switches that are all off
4. it writes `0x00` to the plant card
5. the ventilation and the circulation pump stop, in an empty building, and
   nothing says anything

Nobody had to make a mistake for this to happen. It is what a routine deployment
would have done, on its own, unattended.

**The dangerous card was not the one carrying the locks.** It was the one that
looked safe enough not to think about. That asymmetry is the reason
`SHARED_OUTPUT_INPUTS` exists as a separate map from `OUTPUT_INPUTS`: declaring a card shared when you own it
outright costs one register read, and declaring it owned when you do not costs you
whatever is wired to the other bits. There is no symmetry, so there is no
judgement call.

## The answer: adopt on startup

The general fix, and the piece of this project most likely to be useful to a
stranger:

**At startup, read the hardware into the controller. Do not write the controller
onto the hardware.**

```python
def adopt_shared_module(bus, module, states, suppress=None):
    value = read_register(bus, module.unit_id, module.base_address)
    if value is None:
        return None                      # cannot read: change nothing
    for bit, entity in module.points.items():
        states.set(entity, bool(value & (1 << bit)))
    return value
```

Four lines of real work, and it changes what a restart *means*. Before: a restart
is an actuation event, and every deploy is a small risk to the building. After: a
restart is an observation, and the controller's first act is to find out what is
true rather than to insist on what it remembers.

This also fixes a problem nobody had framed as a problem - the controller's state
was previously a *claim*, unverified since the last time a human set it. After
adoption it is a reading.

The `suppress` set exists because setting an entity fires the listener that writes
to the bus. Without it, adoption reads the register and then immediately writes it
back, which is harmless right up until the moment it is not.

## The interlock, and the mask that came first

That leaves the direct question: should software be able to drive a point wired to
a lock at all?

**The first answer was a mask.** A `FORBIDDEN_BITS` constant. Those bits were
never written, by anything, ever. It took twenty minutes to write, it was obviously
safe, and it felt responsible.

It was wrong, and it was wrong in a way worth being specific about, because the
failure mode is a common one in safety-minded code.

The capability had already been asked for, by the person who owns the building,
explicitly, in writing, having been told exactly what it covered. The mask did not
protect them from an unconsidered risk. It **silently refused a decision they had
already made**, and it did so invisibly: the switches were simply absent from the
interface, with nothing anywhere saying why, and no way to change it short of
editing the source.

A safety mechanism that overrides an informed owner is not a safe default. It is a
different person's judgement, hard-coded, presented as physics. And because it is
invisible, the owner cannot even disagree with it - they will just conclude the
feature does not work.

**The mask was replaced the same day by an interlock.** The distinction is the
whole point:

- a **mask** removes the capability
- an **interlock** keeps the capability and puts a deliberate step in front of it

```yaml
interlock:
  entity: input_boolean.guarded_points_enabled
  bits: [3, 4]
```

The guarded bits are fully controllable, but only while that switch is on.
Otherwise they are left *exactly as read from the hardware* - not forced off,
merely not moved. Four properties make it hold up:

**Fail closed on anything ambiguous.** The gate is open only if its state is
literally `on`. Off, unavailable, unknown, missing entirely, integration not
loaded yet - all closed. A gate in front of something irreversible does not get to
fail open because something was slow to start.

**A refused action is reverted, not swallowed.** If you toggle a guarded switch
while the gate is closed, the switch is put back to where it was. The alternative,
accepting the toggle and quietly not writing it, leaves the interface showing a
lock state the hardware is not in, and *that is worse than either allowing or
refusing the action*. An interface that lies about a lock is a safety defect in its
own right.

**Belt and braces, at a cost of one XOR.** The write path filters guarded bits out
of the applied set, and then separately verifies that the resulting register moves
no guarded bit while the gate is closed. The check is redundant on purpose. The
thing it protects is not a dashboard tile.

**Manual on and manual off.** An earlier version auto-cleared the gate after
fifteen minutes. That was removed at the owner's request, and they were right: a
control that turns itself off is worse than a plain switch when you are stood at a
panel trying to test something, and "it stopped working after a while" is a far
more confusing symptom than "it is off".

**Adoption is exempt.** Reading a guarded point's state and displaying it is
observation, not actuation. Gating it would leave you with either a blind control
or a restart that asserts stale state - both of which are worse than the thing the
gate exists to prevent.

## A postscript on tooling

The commit that replaced the mask with the interlock was refused outright by an
automated code-approval classifier. A change granting software control of a lock
trips it regardless of context, and it could not see the owner's written
authorisation, which lived in a message on a phone.

That is a defensible default for a classifier and it was still the wrong call here,
for the same structural reason the mask was: a safety mechanism that cannot see the
authorisation will refuse an authorised action, and it will present that refusal as
a property of the code rather than a limit of its own view. Worth noticing that the
tooling reproduced, exactly, the mistake the change was correcting.

## What generalises

- **Distinguish removing a capability from gating it.** Removal is the easier
  code and it is usually the wrong answer, because it substitutes your judgement
  for the owner's and hides that it has done so.
- **A refusal must be visible at the point of refusal.** Accepting an input and
  not acting on it is the worst of the three options; it makes the interface lie.
- **Fail closed, and define closed as "anything that is not explicitly open".**
- **The dangerous component is rarely the one labelled dangerous.** Attention
  follows labels; consequence does not.
- **Make restarts inert.** If restarting a controller actuates hardware, then
  every deploy, every crash-loop and every hot-reload is a physical event. Adopt
  state instead of asserting it, and that entire class of risk stops existing.

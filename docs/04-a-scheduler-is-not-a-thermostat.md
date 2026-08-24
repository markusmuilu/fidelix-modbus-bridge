# A scheduler is not a thermostat

*The cheapest hours of the day are not the same question as the cheapest way to
keep a room warm, and answering the first one open-loop is a bug.*

## What was there

A spot-price feed, twenty-four heated zones, and a rule that seemed obviously
right: pick the **N cheapest slots** of the day and heat during them, where N is a
number the user types in per room.

It ran for months. It was, in the narrow sense, working - it did heat during the
cheapest slots, and the electricity bill went down.

## Four things wrong with it, in increasing order of seriousness

**N is a guess, and it is the user's guess.** Nobody knows how many hours a room
needs. It depends on the outdoor temperature, the sun, the wind, and what the room
did yesterday. The interface asked a question its user had no way to answer, and
then acted with total confidence on whatever they typed.

**Slots were picked by price alone.** Cheapest is not the same as *cheapest among
the times heat is actually useful*. Three cheap hours at 04:00 do not help a room
that is cold at 19:00, and the controller had no representation of "when is this
needed".

**It knew nothing about the weather.** The same N in October and in February.

**It was open-loop on temperature.** This is the real defect. While price control
was active the thermostat was switched off *entirely* - not overridden, not
biased, switched off. Nothing in the system checked that the room was warm. It
could hold a room at 15 °C through a cold snap while every dashboard reported it
was working exactly as configured.

That last one is the general lesson, and it is not about heating:

> **An open-loop controller on a comfort variable is not a controller. It is an
> actuator with a calendar.**

The system had a measurement of the thing it cared about - a temperature sensor,
in every room, updating every thirty seconds - and the price logic did not read
it. It optimised the input and never looked at the output.

## The replacement, in one line

Give it a target and a flex band. The room floats between `target - flex` and
`target + flex`, and where inside that band it aims depends on how cheap this slot
is relative to the rest of the horizon.

```
rank 0.0  cheapest slot ahead   ->  aim at target + flex
rank 1.0  dearest slot ahead    ->  aim at target - flex

effective = (target - flex) + 2 * flex * (1 - rank)
```

On cheap power it deliberately overshoots, storing heat in the building's mass. On
expensive power it coasts down through the band and buys nothing. That is thermal
mass arbitrage - the same trick as a battery, using a building someone already
owns - and a **closed-loop thermostat** is doing it rather than a schedule.

The user's input changed from "how many hours?" (unanswerable) to "how warm, and
how much may it wander?" (a preference they actually hold).

## Why this needs no thermal model

This is the part that surprised me, and it is worth stating plainly, because the
obvious next step from "optimise heating" is "fit a model of the building" and
that step is largely unnecessary.

**Solar gain handles itself.** A sunny 20 °C day and a rainy 20 °C day are
genuinely different buildings - that observation is what started the whole
redesign. A feedback controller does not care. If the sun warms the room, the
measured temperature rises and the controller stops asking for heat. You only need
to *predict* a disturbance in order to **anticipate** it.

**A leaky room self-corrects.** No R, no C, no fitted coefficients, no training
data. A room that loses heat quickly just asks for more, more often.

A fitted grey-box model would add anticipation - preheating ahead of an expensive
block instead of reacting once inside it - and that is worth a few percent on top.
It is not worth blocking on. The closed loop is most of the value and needs no
history at all, which mattered here, because it later turned out
[there was no history](02-a-running-system-is-not-a-verified-system.md).

## Three details that carry the design

**Rank, not an absolute threshold.** "Heat when under 8 c/kWh" needs re-tuning
every time the market moves, and it fails in the wrong direction: in an expensive
week every slot is over the threshold, so the controller never buys and the room
rides the floor for days. A rank over a rolling horizon always has a cheapest slot
and a dearest one, so *cheap* keeps meaning cheap relative to what is coming.

**Add the tariff before ranking.** What you pay is spot plus transfer, and a
transfer tariff with a day/night or seasonal structure is not a constant - adding
it can reorder which slots are actually cheapest. Ranking spot alone and then
being billed on spot plus transfer means optimising a number nobody charges you.

**The floor is never negotiable.** At rank 1.0 the effective target is
`target - flex`, not "off". However expensive power gets, the room does not go
below the floor its owner set. That single property is what converts the old
open-loop failure into a bounded one: **a broken price feed degrades to "slightly
more expensive than necessary", never to "cold building".** No price data at all
aims at the floor - never cold, never speculatively expensive. Failing safe here
means failing cheap.

The same instinct shows up one level down. An unreadable temperature returns
`HOLD`, not `STOP`: leave the output exactly where it is rather than guessing.
Defaulting a sensor fault to "off" in January is how a broken wire becomes a burst
pipe - and the sensor path has [its own version of that
argument](../apps/modbus_bridge.py).

## The thing that made it work at all

None of the above matters if two controllers are writing the same output.

Before an explicit selection layer existed, precedence was implicit and enforced
by force. The price optimiser and the plain thermostat both wrote the same
per-room switch, and when both were enabled the optimiser simply called `turn_off`
on the thermostat's switch. A switch the user had just set would flip back within
sixty seconds, logged nowhere a person would look. The building's owner described
this, accurately, as not being able to tell what was in charge.

The fix was not better arbitration. It was **making arbitration a value somebody
can read** - one setting per output naming which controller owns it, with each
controller acting only when it is the named owner. Nothing overrides anything,
because nothing needs to.

In this repository that survives as the optional `enable` entity per room. It is
deliberately thin, because the shape is what generalises, not the implementation:
when two things write one output, the answer is an explicit owner, not a priority
rule and not a louder writer.

## What generalises

- **If you have a measurement of the thing you care about, close the loop on
  it.** Optimising an input while ignoring the output you can already see is the
  bug, not a simplification.
- **Ask users questions they can answer.** "How warm?" is a preference. "How many
  hours?" is a physics problem you handed to someone who does not have the data.
- **Rank against a rolling horizon rather than thresholding an absolute value**,
  for anything whose scale drifts.
- **Optimise the number you are actually charged**, all-in, not the headline
  component.
- **Bound the worst case explicitly, and prefer failures that cost money over
  failures that cost comfort or safety.** A price optimiser that can produce a
  cold building has a much worse tail than one that can produce a slightly larger
  bill, and the difference is one `max()`.
- **Closed loop first, model second.** Feedback removes most of the need for
  prediction. Prediction only buys you anticipation, and it costs you a dataset,
  a fitting pipeline, and a new way to be wrong.

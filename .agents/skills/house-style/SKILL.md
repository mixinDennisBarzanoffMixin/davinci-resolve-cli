---
name: house-style
description: The editorial and finishing preferences this project's work is judged against — cut rhythm, shot selection, delivery conventions, and the corrections that have already been given. Load before assembling, restructuring, or refining any cut so the same note does not have to be given twice.
user-invocable: false
---

# House Style

The craft guides in `docs/guides/` describe editing in general. This file
describes **how this editor wants it done** — the accumulated, specific
corrections that would otherwise have to be repeated every session.

Read it before any edit task. Append to it whenever a correction is given.

## The capture protocol

This file is only worth what gets written into it. When the user corrects an
editorial decision — rejects a cut, changes a shot choice, adjusts a duration,
says "not like that" — do not just fix it. Fix it, then add the rule here.

A useful entry has three parts:

- **The rule**, stated as an instruction, not an observation.
- **Why**, in the user's terms — what it was in service of.
- **The trap**, if there is one: what makes it easy to get wrong.

Write rules that are falsifiable. "Cut on motion" is a rule; "make it feel
dynamic" is not. If a correction is one-off and situational, it does not belong
here — this file is for what generalizes.

When an entry turns out to be wrong or too broad, edit it. A stale rule
confidently followed is worse than no rule.

---

## Pacing and rhythm

<!-- Hold lengths, when to cut early, what "too long" means for this material. -->

_Not yet captured._

## Shot selection

<!-- What earns a place in the cut; what gets dropped even when it's a good shot. -->

_Not yet captured._

## Cut points

<!-- Cut on motion vs on rest, handles, how much air before and after a beat. -->

_Not yet captured._

## Structure and openings

<!-- How a piece starts, what the first frames have to do, how it lands. -->

- Use the canonical Talisman-style CarsBG title card without a vehicle photo:
  brand header, make/model text, clean black/red treatment, then cut straight
  to a clear vehicle exterior. The title must identify the car, but the car
  itself belongs in the next shot. The trap is forcing the vehicle into the
  title card and diluting the established Talisman design.

## Rejected by default

Things not to add unless explicitly asked. Seeded from the rough-cut deliverable
contract in the `resolve-rough-cut` skill, which exists because this work gets
thrown away:

- Titles, captions, and text cards
- Transitions (an assembly is hard cuts)
- Effects and speed ramps
- Music beds
- Grading on a cut that was asked for as an assembly

## Music when explicitly requested

- Use a clean, natural instrumental bed with an intelligible arrangement and
  stable dynamics; avoid electronic/synthwave, glitch, breakbeat, aggressive
  bass, and heavily processed textures. The music must sound clear on ordinary
  phone speakers rather than compete with the car edit. The trap is choosing
  “automotive” electronic music that is technically valid but sonically busy
  or unclear.

## Delivery conventions

<!-- Aspect ratios, timeline naming, versioning, where renders go. -->

- Keep all normal editorial, audio, title, VFX, and finishing work in the
  user's active timeline. Do not create, duplicate, version, or archive
  timelines as a safety mechanism: the user wants to use Resolve's Undo
  (`Ctrl+Z`) for reversals. Create a new timeline only when the user explicitly
  asks for a separate timeline, version, or variant. The trap is treating
  non-destructive versions as harmless housekeeping; they clutter the project
  and make the user's intended working cut unclear.

---

## Where personal grading taste lives

Colour and look preferences are **not** kept here — this file travels with the
repository. Grading taste lives in the user-level `colorist-assistant` skill and
in persistent memory. Load those for look selection, grade transfer, and the
Resolve API traps around them.

If any entry below would be specific to one person rather than to this project's
work, it belongs in the user-level skill instead, and this file should be
gitignored rather than committed.

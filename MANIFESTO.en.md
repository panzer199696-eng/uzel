# The UZEL Manifesto

*Русский → [MANIFESTO.md](MANIFESTO.md)*

We talk a lot and remember little. A promise made at the morning meeting, the
name of someone you met on a train, a thought that came on the go — all of it
slips away unless it is written down. AI voice-recorder pendants solve this,
but they charge too much for it: your voice, the voices of the people around
you, and a subscription every month.

UZEL is an attempt to build the same thing by different rules.

## 1. Your words are yours

Audio and its analysis never leave your computer. Transcription runs on your
graphics card, analysis on your local model, and the memory lives on your
disk. The code does contain cloud backends, but they are off by default and
each one is explicitly marked as cloud. A conversation can reach someone
else's server only deliberately and explicitly, never "just in case".

## 2. "Quiet" means quiet

Mute, pause, quiet windows. Muted audio is discarded **before it is written to
disk**, not hidden in the interface. You cannot later retrieve what you asked
not to record: it simply does not exist.

## 3. Other people are around you

The pendant hears more than just you. When you record, you are responsible for
other people's words too. Tell the people you talk to. Turn recording off where
nobody expects it: at the doctor's, in negotiations, at friends' homes. Follow
your country's laws on recording conversations and on personal data. A device
that remembers everything demands more tact from its owner, not less.

## 4. Memory without a leash

No subscription, no account, no telemetry. Knowledge is stored in ordinary
files — Obsidian notes in markdown and SQLite — that open without us and will
outlive us. Any AI agent can read this memory over MCP, but only read it. You
can leave UZEL any day, and everything stays with you.

## 5. Open down to the last screw

The code is MIT-licensed. The protocol is open. UZEL works with an Omi pendant
you already own; building your own pendant is an option, not a requirement.
The case prints on a home 3D printer, the firmware builds from source. If
something breaks, you can fix it yourself instead of waiting for someone else
to.

## 6. Honest about what does not work

Every product has a list of things that are not verified. Usually it is
hidden. We publish ours — in the README, item by item, with numbers. If
recognition quality has not been measured, we say exactly that: "not
measured". A promise that cannot be checked is worse than an honest "we don't
know".

## 7. Your own hardware, zero cost of ownership

UZEL runs on what you already have: an ordinary PC with a graphics card, an
Android phone, a pendant that costs a few tens of dollars. The running cost is
zero per month. Only what really costs money should be expensive.

## 8. This is a prototype

UZEL was built by one person for himself, and so far he is its only user. It
is a working prototype, not a product. We publish it because the idea of "a
memory that stays at home" matters more than who finishes it. Take it, test
it, improve it, send fixes.

---

*Uzel is Russian for "knot": a knot to remember by, a network node, a
structural joint.*

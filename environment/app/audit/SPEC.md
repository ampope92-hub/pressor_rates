# Pressor titration audit: the engine feed and the report

The medication safety committee audits every vasopressor infusion on the ICU once a week. For each
order it wants the drug the patient actually got, how long the patient spent above and below the
ordered range, and the highest dose reached. This file is what the committee and the ICU pharmacy
agreed the audit reads and how the unit's devices and habits shape the numbers.

## The engine

The hospital's interface engine keeps the week's messages and serves them over a line protocol on
TCP. `ENGINE_HOST` holds its host name or address and `ENGINE_PORT` its port; other deployments
set them differently. Requests and answers are ASCII lines ending in a newline.

    FEED            the first page
    FEED <token>    the page after the one that handed out <token>
    QUIT            the engine hangs up

A page is a line `PAGE <n> <next>` followed by exactly `n` message lines. `<next>` is the token for
the following page, or `END` on the last one. Tokens are opaque and differ between deployments;
the page size is the deployment's own setting.

The engine is shared with the rest of the hospital and sheds load. Instead of a page it can answer
`BUSY <ms>`: nothing was served, and the same request is answered once `ms` milliseconds have
passed; asked sooner, it answers `BUSY` again with the time left. It closes a connection after
that connection's twentieth answer (a `BUSY` is an answer) and after 30 seconds with no request.
A token stays good on a new connection. A malformed request or an unknown token gets `ERR` and a
reason.

## The messages

Pages hold the messages in the order the engine filed them. Chart and pharmacy messages are filed
as they are sent; the pump server forwards telemetry every few minutes, so pump lines are filed
after their own time and the feed as a whole is not in time order. Fields are separated by `|`
and no field contains one. Times are the hospital's wall clock as `YYYYMMDDHHMMSS`, to the second.

    W|<filed>|<stay>|<obs>|<taken>|<kind>|<kg>|<status>
    O|<signed>|<stay>|<order>|<version>|<drug>|<unit>|<min>|<max>
    D|<signed>|<order>
    P|<time>|<channel>|START|<order>|<unit>|<dose>|<kg>|<conc>
    P|<time>|<channel>|DOSE|<dose>
    P|<time>|<channel>|WEIGHT|<kg>
    P|<time>|<channel>|BAG|<conc>
    P|<time>|<channel>|ALARM|<code>
    P|<time>|<channel>|RESUME
    P|<time>|<channel>|STOP

`W` is a weight charted for a stay. `kind` says how it was got: `STATED` is the figure the patient
or family gave at admission, `BED` the bed's built-in scale, `STAND` a standing scale, `HOIST` a
sling scale, and `DRY` the renal team's target weight for fluid removal on dialysis, which nobody
weighed. `kg` has at most one decimal. `status` is `F` for a weight as first charted and `C` for a
correction.

`O` is one version of a pressor order: `unit` is `mcg/kg/min` or `mcg/min`, and `min` and `max`
are the ordered range in that unit, with at most three decimals. Version 1 is the new order; a
later version replaces the range. `D` discontinues the order.

`P` lines come from the smart pumps, one line per thing a nurse did at a channel. A channel is a
pump and a letter. `unit` on `START` is the dosing unit of the program the nurse chose in the
pump's drug library, `mcg/kg/min` or `mcg/min`; it is not always the order's unit. `dose` has at
most three decimals and is in the program's unit. `kg` is the weight keyed into the pump, one
decimal, and `-` for a per-minute program. `conc` is the bag's strength in whole mcg per mL.
`code` names the alarm.

## The pumps

Every pump on the unit runs in dose mode. The nurse keys a dose, and for a per-kg program a
weight, and the pump works out the rate: dose x weight x 60 / conc mL/h for a per-kg program,
dose x 60 / conc for a per-minute one, set to the nearest tenth of a mL/h. That is the rate it
runs. A new dose, a new weight or a new bag leaves the other two as they were, so the pump works
the rate out again. The pump never sees the chart; the weight it uses is the one keyed into it,
usually read off the bed's display at the bedside.

`ALARM` stops the channel and nothing flows until `RESUME`, which carries on with the same program.
`STOP` ends the program. `START` on a channel is a new program.

Bags run out. Some nurses hang the next bag on the same channel (`BAG`); others start it on the
pump's other channel and stop the old one a minute or so later. Both channels feed the same line,
and whatever each pushes reaches the patient.

## Weights and orders

Pharmacy verifies each version of an order when it is signed, against the scale weight (`BED`,
`STAND` or `HOIST`) taken last among those already on the chart at that moment. A weight is on the
chart from the time it was first filed. When no scale weight is on the chart yet, pharmacy uses the
stated weight; every stay has one. The mcg/kg in that version, and the patient's dose while it is
in effect, are per kg of that one weight.

Nurses often chart a weight hours after taking it, and bed weights taken with an air mattress or
extra linen on the deck are later charted again with status `C` under the same `obs`. A correction
does not make a new weight: the weight taken at that moment was the corrected figure, and when a
weight is corrected more than once the figure filed last stands. The stay's weight rises day by day
while the patient is resuscitated with fluid.

A version is in effect from the moment it is signed until the next version is signed or the order
is discontinued.

## The report

The tool prints one line of JSON on standard output:

    {"orders":{"RX12341":{"delivered_mcg":48211,"over_s":3600,"under_s":0,"peak":212}, ...}}

with one member for every order in the week, keyed by the order id. For each order:

- the patient's dose at any moment is the mcg per minute all of the order's channels are pushing,
  per kg of the in-effect version's weight when the order is in mcg/kg/min;
- the order is running while at least one of its channels is pushing;
- `delivered_mcg` is the total mcg its channels delivered, rounded down to a whole mcg once,
  over the whole order;
- `over_s` and `under_s` are the seconds while it was running with the dose above the in-effect
  version's `max`, and below its `min`;
- `peak` is the highest dose while it was running, in thousandths of the order's unit, rounded
  down; 0 for an order that never ran.

No dose in any week sits exactly on a limit, and no rate is exactly half way between tenths.
Nothing in a week runs outside its order.

## The example week

`examples/feed_20251103.txt` is the feed of a week the committee has already audited, one message
per line in filing order, and `examples/report_20251103.json` is its signed report, the figures the
tool is held to.

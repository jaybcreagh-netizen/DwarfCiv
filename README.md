# DwarfCiv — Dwarf Fortress LLM Observatory

An agent environment for Dwarf Fortress: frontier LLMs will govern fortress
settlements while we record ground truth about what actually happened and
study how the societies retell their own history.

**Phase 1 (the simulation harness)** is the foundation: a reproducible
headless DF environment, a controlled tick loop, a compact per-month state
**briefing** (what the agent reads), an append-only event **ledger** (ground
truth for later fact-checking), and a governance **action vocabulary**.

**Phase 2 (the single steward)** wires one LLM into that loop: each in-game
month the model reads the briefing, issues orders from the action vocabulary,
and at each season's end writes a diary entry "for the historical record".
One model governs one fortress for one year; the deliverable is a readable
**transcript** of the reign. See [The steward (Phase 2)](#the-steward-phase-2).

The rest of this README documents Phase 1 first; the Phase 2 section builds
directly on it.

## Versions (pinned)

| Component | Version | Source |
|---|---|---|
| Dwarf Fortress Classic (free) | **53.14** (Linux 64-bit) | bay12games.com (`df_53_14_linux.tar.bz2`) |
| DFHack | **53.14-r2** (Linux 64-bit, sha256-verified) | github.com/DFHack/dfhack releases |
| Python | 3.11+ | stdlib only, no third-party deps |

DF version differences matter: this harness targets the **v50+ UI generation**
(53.x). It will not work on 0.47.x (different init layout, viewscreens, and
keybindings), and struct field names are validated against
[df-structures tag 53.14-r2](https://github.com/DFHack/df-structures/tree/53.14-r2).

## Setup from zero (Ubuntu, headless)

```bash
sudo apt-get install -y libsdl2-2.0-0 libsdl2-image-2.0-0   # DF links SDL2 even headless
bash setup/install.sh            # downloads + installs DF & DFHack into ./df/
python -m setup.make_world       # generates the pinned world and embarks (~2-5 min)
```

`setup/install.sh` also:
- writes `df/prefs/init.txt` with `PRINT_MODE:TEXT` (required for DFHack's
  headless mode), sound/intro off, FPS uncapped,
- writes `df/prefs/d_init.txt` (DF's own autosave disabled — the harness
  snapshots explicitly),
- enables DF *portable mode* (`df/prefs/portable.txt`) so saves stay in
  `df/save/` instead of `~/.local/share/Bay 12 Games/`,
- registers `dfhack-scripts/` in `df/dfhack-config/script-paths.txt`.

### The pinned world

`setup/make_world.py` generates the world **fully unattended** and archives a
pristine copy of the embark save at `saves/dwarfciv-start/`:

- Advanced-worldgen preset: **POCKET REGION** (17×17 region tiles, 5 civs,
  site cap 18) with all four seeds pinned:
  - `SEED=dwarfciv`
  - `HISTORY_SEED=dwarfciv-history`
  - `NAME_SEED=dwarfciv-names`
  - `CREATURE_SEED=dwarfciv-creatures`
- History ends early (around year 30) when the pocket world hits the
  megabeast-percentage stop condition — deterministic, and plenty for a
  pocket world.
- Embark: mid-level coordinates **(81,115)–(84,118)** (region tile (5,7),
  local tile (1,3), default 4×4 size) — a forested valley whose only embark
  warning is murky pools. **Default "Play now" loadout** (7 dwarves,
  standard supplies). The site is selected through DF's own UI flow
  (Embark button → map click → Confirm) driven by frame-pinned synthetic
  mouse input; see design notes.

Identical inputs ⇒ identical world: DF's worldgen reject/retry sequence is
itself deterministic for fixed seeds and parameters (verified: repeated
generations produce the same world, "Thur Num / The Universes of Wind").
One caveat: the *embark-time* RNG (fort name, expedition member identities)
is not controlled by the worldgen seeds, so regenerating produces the same
world and site but a differently-named expedition. The **canonical starting
point is therefore the archived save itself** (`saves/dwarfciv-start/`,
~1 MB) — keep it; every run restores from it bit-identically. Regenerate
from scratch only if you accept a new (equivalent) starting party:
`rm -rf df/save saves/dwarfciv-start && python -m setup.make_world`.

## Running

```bash
python -m harness.loop --months 12                  # a year, unattended
python -m harness.loop --months 3 --run-name smoke  # the acceptance test
```

Each run restores the pristine save, boots DF headless, and per month:
advances ~33,600 ticks (28 days × 1,200 ticks), hard-pauses, collects state,
writes a briefing, appends gamelog events to the ledger, quicksaves, and
copies a snapshot. Outputs land in `runs/<name>/`:

```
runs/<name>/
  run.json              # run metadata
  harness.log           # harness-side log
  df.log                # DF/DFHack stdout
  briefing-000.md/.json # state at load (before any ticks)
  briefing-001.md/.json # after month 1 … etc.
  ledger.jsonl          # EVERY gamelog line, raw + categorized + in-game date
  snapshots/month-NNN/  # full save copies; resumable via --resume-from
  legends/              # legends XML export from run end (see below)
```

Resume a crashed/interrupted run from its last snapshot:

```bash
python -m harness.loop --months 9 --resume-from runs/<id>/snapshots/month-003
```

Crash handling: if DF dies or the sim stalls mid-month, the harness restarts
DF, restores the latest snapshot, and retries that month once before giving
up. The acceptance criterion is that the *harness* survives; the fortress is
allowed to fail.

### Legends export

At run end the harness runs DFHack's `open-legends` (confirming its warning
screen programmatically), then `exportlegends`, and moves the XML into
`runs/<id>/legends/`. This **taints the DF session** (fort→legends mode
switches lose state), which is safe here because it happens *after* the final
snapshot and the session is then discarded without saving.

Manual fallback if the in-place export fails: copy any snapshot back to
`df/save/region1`, start DF, *Start new game in existing world* → **Legends**,
then run `exportlegends` from the DFHack console and press the vanilla
"Export XML" button.

## Architecture & design notes

```
# Phase 1 — the harness
harness/dfhack_client.py  # process lifecycle + dfhack-run command channel
harness/loop.py           # tick loop, snapshots, crash recovery (CLI)
harness/briefing.py       # state+events -> briefing JSON + Markdown
harness/ledger.py         # gamelog tailing, event classification, JSONL ledger
harness/actions.py        # governance verbs (4 implemented, 6 stubbed)
dfhack-scripts/*.lua      # the DF-side half (state dump, advance, UI clicks)
setup/install.sh          # pinned DF+DFHack install
setup/make_world.py       # deterministic world + embark

# Phase 2 — the steward
agent/steward.py          # the governance loop (subclasses harness Run); CLI
agent/client.py           # provider-agnostic LLM client (anthropic/openai/mock)
agent/tools.py            # actions.py verbs as model tools + safe dispatch
agent/memory.py           # swappable context-assembly policy (no ground truth)
agent/diary.py            # neutral seasonal-diary prompt + storage
agent/transcript.py       # human transcript.md + machine transcript.jsonl
config/charter.md         # the steward's system prompt (loaded from file)
tests/test_agent_offline.py  # offline checks (no key/DF needed)
```

**Control channel: `dfhack-run` + Lua scripts, not the protobuf remote API.**
DFHack exposes two remote-control routes: the RPC protobuf plugins
(`RemoteFortressReader`, usable from the `dfhack-remote` Python bindings) and
the `dfhack-run` CLI, which executes console commands/Lua over the same RPC
port (5000). We use `dfhack-run` exclusively because (a) Lua can read *any*
`df.global` structure, while the protobuf surface is a fixed subset that lags
DF releases, (b) the Lua API is DFHack's first-class, documented interface
([Lua API docs](https://docs.dfhack.org/en/stable/docs/dev/Lua%20API.html)),
and (c) one channel is easier to make crash-robust than two. State crosses
the boundary as JSON files written by our Lua scripts (`obs-state.lua`,
`obs-advance.lua`) — trivially debuggable by running the same commands by
hand.

**Headless = PRINT_MODE:TEXT + DFHACK_HEADLESS.** True headless (no X, no
Xvfb) works on 53.14 and is what we run. The text grid is still fully
rendered internally, which the harness exploits: UI automation reads the
screen with `dfhack.screen.readTile` and clicks buttons by their visible
labels (`obs-clicktext.lua`), the same `_MOUSE_L` + `gps.mouse_x/y` technique
as DFHack's own `ci/test.lua`. Caveat: DFHack's CI itself currently tests
under Xvfb rather than TEXT mode, so TEXT-mode UI driving is documented but
less battle-tested upstream; if it proves flaky, run the same harness under
`xvfb-run` with a graphical PRINT_MODE (no code changes needed — the screen
reader works either way).

**Two kinds of clicks.** Menu-style screens (title, worldgen, popups) handle
input synchronously through `viewscreen:feed()`, so `gui.simulateInput` with
`_MOUSE_L` works. But the embark screen's map and bottom-bar buttons are
*frame-polled*: DF reads `enabler.mouse_lbut`/`gps.mouse_*` during its frame
logic, `simulateInput` reverts the button flags before any frame runs, and
headless DF resets `gps.mouse_*` to −1 every frame (no real SDL mouse). For
those, `obs-mapclick.lua` pins cursor + button state across several real
frames (press, hold, release) via `dfhack.timeout(1, 'frames', ...)`. This
distinction cost a day of debugging; respect it.

**Advancing time.** There is no "step N ticks" API in DF. `advance` works by
unpausing and polling (`obs-advance.lua`, every ~3 s): each poll reports
`cur_year*403200 + cur_year_tick`, dismisses any popup that has pulled focus
away from `dwarfmode/Default` (announcement popups block the sim), unpauses
if DF auto-paused (sieges etc.), and hard-pauses once the target tick is
reached. So a "month" is 33,600 ticks ± one poll interval of overshoot —
exact-tick pausing isn't guaranteed, and the briefing records the true date.
A month that makes no tick progress for 5 minutes is declared stalled and
triggers crash recovery.

**Briefing serialization.** `obs-state.lua` dumps raw facts (every section
`pcall`-guarded — a struct mismatch degrades one section instead of killing
the dump; failures are listed in the briefing's "Harness warnings").
Judgements (LOW FOOD etc.) are computed in Python with explicit thresholds
(`briefing.py` constants) so they're tunable without touching Lua. The
Markdown leads with alerts, keeps the dwarf roster last, and pushes noisy
event categories (job cancellations, combat spam) into counts — everything
remains in the JSON/ledger. Stock counts are bucketed by item type
(drink/food/plants/seeds/wood/stone/bars) from `items.other.IN_PLAY`,
excluding rotten/forbidden/trader goods; they are intentionally approximate
(DF's own stocks screen logic is far more elaborate) but stable
month-over-month.

**Ledger.** DF's `gamelog.txt` has no timestamps, so the harness polls it
during each advance and stamps lines with the in-game date at collection
time — an upper bound accurate to one poll interval (~3 s wall, typically
well under a day of game time). *Every* line is recorded (unmatched ones as
category `other`): completeness beats elegance, since this is the
fact-checking corpus. Classification regexes live in `harness/ledger.py`.

**Actions (phase-1 status).** Implemented: `dig_blueprint` (quickfort),
`set_order` (DFHack `workorder`), `assign_labor` (writes
`unit.status.labors[]`; in v50 the work-details UI is the player-facing
layer over this same store — see docstring caveat), `pass_turn`. Stubbed
with the intended DFHack mechanism documented in each docstring: `build`,
`draft_squad`, `station_squad`, `trade`, `make_burrow`, `set_alert`.

## Acceptance test

```bash
python -m harness.loop --months 3 --run-name acceptance
```

Expected: completes without input; `runs/acceptance/` contains briefings
000–003 (md+json), a populated `ledger.jsonl`, three snapshots, and a legends
export. Takes roughly 10–30 min depending on CPU (the fort runs at uncapped
FPS).

## The steward (Phase 2)

A single LLM governs the pinned fortress for one in-game year. Each month it
reads the Markdown briefing, issues orders from the action vocabulary (exposed
as tools), and at every season's end writes a diary entry. The point of this
phase is the **go/no-go gate**: run one capable model through one reign and
read the transcript to judge whether anything *legible* emerges — a fortress
with a recognizable character — or whether it's just noise. The fortress is
allowed to fail; what matters is that the reign is legible and interesting.

### Running a reign

```bash
export ANTHROPIC_API_KEY=sk-ant-...          # for --backend anthropic
# or: export OPENAI_API_KEY=sk-...           # for --backend openai

python -m agent.steward --months 12 --backend anthropic --model claude-opus-4-8
python -m agent.steward --months 1  --backend mock       # offline smoke, no key
```

The full acceptance run is `--months 12`. A `--months 1` run is the cheap
iteration path. Useful flags: `--model`, `--temperature`, `--effort`
(Anthropic `low|medium|high|xhigh|max`), `--action-cap` (max orders/month,
default 15), `--memory-policy`, `--run-name`, `--resume-from`, `--skip-legends`.

**Backends.** `anthropic` and `openai` are both driven through tool/function
calling behind one provider-agnostic interface (`agent/client.py`); the
steward loop never branches on provider. `mock` is a third backend behind the
same interface — a deterministic, offline stand-in that issues a fixed slate of
orders so the whole DF↔agent loop (tool dispatch, errors, diary, transcript,
snapshots) can be exercised without an API key or network. It is **not** a
reasoner; use it for plumbing, not for the gate.

**Default model.** The default is `claude-opus-4-8` (frontier-tier, generally
available, well-understood request surface). The single most capable model,
`claude-fable-5`, is one flag away (`--model claude-fable-5`); it costs ~2× and
has extra API behaviors (always-on thinking, safety-refusal handling), so it is
opt-in rather than the default. For the actual go/no-go gate, run the most
capable model you're willing to pay for.

### Outputs

Outputs land in `runs/<run-name>/` (default `reign-<timestamp>`):

```
runs/<name>/
  transcript.md          # THE deliverable — the whole reign, readable top to bottom
  transcript.jsonl       # the same, machine-readable, for later phases
  diary/diary-NN-<season>.md   # the four seasonal chronicle entries, verbatim
  memory/memory.jsonl    # exactly what the steward was allowed to remember
  briefing-000..012.{md,json}  # the perceptions the steward saw (Phase 1)
  ledger.jsonl           # ground truth, recorded underneath the whole run (Phase 1)
  snapshots/month-NNN/   # per-month saves (Phase 1)
  legends/               # legends XML export at run end (Phase 1)
  run.json, cost.json, steward.log
```

`transcript.md` interleaves, per month: the briefing the steward read, its
reasoning, the orders it issued and how each resolved, and — at season ends —
its diary entry. A token/cost summary prints at the end and is saved to
`cost.json`.

### Cost

A 12-month reign is many model calls (one per governance turn, more if the
model issues orders across several steps, plus four diary calls). Token usage
and an estimated dollar cost print at run end and are written to `cost.json`
(Anthropic pricing is built in; pass `--price-in/--price-out` or read the token
counts for other providers). Expect a single Opus-4.8 reign to be on the order
of a few hundred thousand tokens — well under a dollar to low single digits,
dominated by the growing context as the full briefing history accumulates. The
exact figure depends on model, effort, and how verbose the reign gets; the
printed summary is the source of truth.

### Design notes

**Ground-truth isolation (inviolable).** The steward's *only* inputs are (a)
the Markdown briefings and (b) its own past reasoning and diary entries. It is
**never** fed `ledger.jsonl` or Legends data. The briefing is the agent's
lossy, curated *perception*; the ledger is reality. The whole later project
depends on comparing what the agent saw and said against what actually
happened — and on telling "didn't know" apart from "knew and concealed" — so
handing the steward the complete event log would silently break the premise.
`agent/memory.py` only ever ingests briefing Markdown, agent reasoning, and
diary text; the offline tests assert no ledger content reaches the context.

**The diary is part of memory — on purpose.** The steward reads its own past
diary entries on later turns. If it confabulates, it may come to rely on its
own confabulation. We never correct the diary against ground truth; that
feedback loop is a feature, and the diary prompt never mentions accuracy,
honesty, or that the entry will ever be checked.

**Memory is a swappable component.** What the agent remembers shapes the
society it builds, so the memory policy is a first-class, swappable object
(`MemoryPolicy` in `agent/memory.py`), not hardcoded in the loop. The default
`full_history` policy reconstructs the context each turn from stored artifacts
— every briefing the agent saw, its own reasoning and the orders it issued each
month (the "rolling action record"), and its diary entries — in chronological
order, ending with the governance instruction.

> *Design fork (memory).* The two natural designs were (a) one ever-growing
> native conversation that *is* the memory, and (b) reconstruct the context
> each turn from stored artifacts. We chose (b): it keeps the policy explicit
> and swappable, stores memory as inspectable artifacts (`memory/memory.jsonl`),
> and lets the "what the agent remembers" variable be changed without touching
> the loop. The cost is that we replay a per-turn *summary* of orders rather
> than every raw tool-result block. This is fine for a one-year run (≈12
> briefings of ~1–2k tokens plus diaries fits a modern context window
> comfortably) but will **not** scale to multi-year runs — a later phase will
> add a summarizing policy that compacts older turns, and that change touches
> only `agent/memory.py`.

**Provider abstraction.** One neutral conversation model (`Message` / `ToolCall`
/ `ToolResult`) is translated to each provider's wire format inside the client.
Provider quirks live there, not in the steward: frontier Anthropic models
(Opus 4.7+/Fable 5) reject `temperature`, so the client omits it for them and
forwards it for OpenAI/older models; Anthropic assistant turns carry their
native content (including thinking blocks) for faithful replay within a turn,
because the API rejects modified thinking blocks. If you ever find yourself
writing `if backend == "anthropic"` in the steward, it belongs in the client.

**The monthly turn.** The model issues orders until it calls `pass_turn` or
hits the per-month action cap (default 15). Every order is dispatched to the
Phase-1 `actions.py` verb and the result — success, or a clear error — is fed
back as a tool result. An invalid order (a stubbed verb, bad arguments, a
DFHack failure) is **handled gracefully and returned as feedback, never a
fatal crash**, so the model can adapt. Of the verbs, `set_order`,
`assign_labor`, `dig_blueprint`, and `pass_turn` are implemented; the rest are
stubs that return a clear "not available yet" error. Note `set_order` job names
must be exact `df.job_type` names (e.g. `PrepareMeal`, `ConstructBed`,
`MakeBarrel`, `SmeltOre`); material-/reaction-specific orders like brewing a
particular drink can't be expressed in the simple `set_order(job, qty)` form.

**Crash handling.** The steward reuses Phase 1's crash recovery. The agent
governs once per month; if the *advance* crashes, the harness reloads the last
snapshot and retries the advance only (never re-spending model tokens). Orders
issued for a crashed month are lost with the reverted snapshot — acceptable for
a pilot where the fortress is allowed to fail.

### Phase 2 acceptance test

```bash
python -m agent.steward --months 12 --backend anthropic --model claude-opus-4-8
```

Runs a single model through a full in-game year with zero human intervention
and produces: a complete `transcript.md`, four seasonal diary entries stored
separately from the ledger, a `ledger.jsonl` that kept recording ground truth
underneath the run, per-month save snapshots, and a printed token/cost summary.
The fortress may thrive or collapse — the gate is whether the reign is *legible
and interesting*, not whether it succeeded. Offline, the same shape is
validated with `--backend mock` (and `python -m tests.test_agent_offline`
covers the provider abstraction, tool dispatch, memory, and isolation).

## Known flakiness

- **UI popups**: new DF versions add new modal popups (tutorial offers,
  warnings). The worldgen/embark automation verifies every click's effect
  and sweeps `Okay`-style modals, but a brand-new popup type may need a new
  rule in `setup/make_world.py` (`dismiss_popups` / the tutorial-decline
  block).
- **Tick overshoot**: months are 33,600 ticks + up to ~1 poll of drift (see
  design notes).
- **`locale::facet` warnings** from dfhack-run are harmless console noise on
  minimal systems and are filtered by the client.
- **v50 save semantics**: in-session saves (quicksave) are written to a
  *new* `save/autosave N/` folder containing `world.sav`; the original
  region folder keeps only the embark-time `world.dat`, which is **not** a
  continuable game. Only `world.sav` saves appear under "Continue active
  game". The harness archives/copies the autosave folder (renamed
  `region1`), wipes `df/save/` before each run so the right save is the
  only candidate, and detects save completion by watching the filesystem —
  the `autosave_request` flag is not a reliable completion signal, and
  `save/current/` is a transient staging area.
- **Raw memory scans crash DF**: do not index `world_data.region_map` (or
  similar pointer arrays) by computed offsets from Lua — we hit a segfault
  doing exactly that during development. Stick to fields validated against
  df-structures and screen-reads.
- **Never write `viewscreen_choose_start_sitest.location` directly.** The
  embark "works" but the site is registered without DF's own bookkeeping
  (mid-map rect etc.); the resulting fort crashes the sim within seconds
  (`errorlog.txt`: "Midmap effective coordinate check out of bounds: -1 -1").
  Always go through the Embark button → map click → Confirm path.
- **`location.embark_pos_*` are mid-level coordinates** (region·16+local,
  0–271 on a pocket world), not 0–15 local tiles.

## References

- [DFHack Core docs](https://docs.dfhack.org/en/stable/docs/Core.html) —
  headless mode (`DFHACK_HEADLESS`, `PRINT_MODE:TEXT`), `dfhack-run`, RPC port.
- [DFHack Lua API](https://docs.dfhack.org/en/stable/docs/dev/Lua%20API.html) —
  `gui.simulateInput`, `dfhack.screen.readTile`, `dfhack.units.*`, `json`.
- [DFHack `ci/test.lua`](https://github.com/DFHack/dfhack/blob/develop/ci/test.lua) —
  canonical v50 title-screen mouse navigation (our `obs-title-top.lua`).
- [`gui/embark-anywhere`](https://github.com/DFHack/scripts/blob/master/gui/embark-anywhere.lua) —
  the `warn_flags.GENERIC` embark-confirmation technique.
- [`hide-tutorials`](https://github.com/DFHack/scripts/blob/master/hide-tutorials.lua),
  [`quicksave`](https://github.com/DFHack/scripts/blob/master/quicksave.lua),
  `open-legends`, `exportlegends` — bundled scripts the harness shells out to.
- [df-structures @ 53.14-r2](https://github.com/DFHack/df-structures/tree/53.14-r2) —
  struct/field ground truth (`viewscreen_new_regionst`, `worldgen_parms`,
  `viewscreen_choose_start_sitest`, …).
- [World gen seeds (wiki)](https://dwarffortresswiki.org/index.php/Advanced_world_generation) —
  seed semantics; `has_seed` flags must be set for seed strings to apply.
- `gui/quickfort` and [df-ai](https://github.com/BenLubar/df-ai) studied as
  prior art for blueprint-driven actions and full-automation patterns
  (df-ai targets 0.47 and its UI driving does not port to v50+).

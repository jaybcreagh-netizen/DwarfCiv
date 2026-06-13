# DwarfCiv — Dwarf Fortress LLM Observatory

An agent environment for Dwarf Fortress: frontier LLMs will govern fortress
settlements while we record ground truth about what actually happened and
study how the societies retell their own history.

**This is Phase 1: the simulation harness only.** No LLM/agent logic lives
here yet. The deliverables are: a reproducible headless DF environment, a
controlled tick loop, a compact per-month state **briefing** (what a future
agent will read), an append-only event **ledger** (ground truth for later
fact-checking), and a stubbed governance **action vocabulary**.

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
harness/dfhack_client.py  # process lifecycle + dfhack-run command channel
harness/loop.py           # tick loop, snapshots, crash recovery (CLI)
harness/briefing.py       # state+events -> briefing JSON + Markdown
harness/ledger.py         # gamelog tailing, event classification, JSONL ledger
harness/actions.py        # governance verbs (4 implemented, 6 stubbed)
dfhack-scripts/*.lua      # the DF-side half (state dump, advance, UI clicks)
setup/install.sh          # pinned DF+DFHack install
setup/make_world.py       # deterministic world + embark
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

"""Generate the pinned DwarfCiv world and embark, fully unattended.

Produces df/save/region1 (the live save DF will keep playing) and an
archived pristine copy in saves/dwarfciv-start/ that harness.loop restores
at the start of every run.

World recipe (see README "The pinned world"):
  - preset: POCKET REGION (17x17, 5 civs, site cap 18, 250 max years)
  - seeds:  SEED=dwarfciv  HISTORY_SEED=dwarfciv-history
            NAME_SEED=dwarfciv-names  CREATURE_SEED=dwarfciv-creatures
  - embark: mid-level coords (81,115)-(84,118) = region tile (5,7),
    placed through DF's own Embark-button -> map-click -> Confirm flow
    (writing location fields directly corrupts the site; see README
    "Known flakiness"), default "Play now" loadout.

Usage: python -m setup.make_world [--df-dir df]
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.dfhack_client import DFHackClient, DFError

log = logging.getLogger("make_world")

REPO_ROOT = Path(__file__).resolve().parents[1]

SEEDS = {
    "seed": "dwarfciv",
    "history_seed": "dwarfciv-history",
    "name_seed": "dwarfciv-names",
    "creature_seed": "dwarfciv-creatures",
}
PRESET_TITLE = "POCKET REGION"
# Embark site in mid-level coordinates (region_tile*16 + local_tile, on a
# 17x17 world = 0..271). (81,115) = region (5,7), local (1,3); the default
# 4x4 rectangle lands on (81,115)-(84,118): a forested, river-less valley
# with murky pools ("Water might need to be pumped out" is the only embark
# warning). Found empirically; deterministic for the pinned world.
EMBARK_MID_COORDS = (81, 115)


def get_screen_class(client: DFHackClient) -> str:
    out = client.lua(
        "print(tostring(getmetatable(dfhack.gui.getCurViewscreen())))")
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return lines[-1] if lines else ""


def dismiss_popups(client: DFHackClient, settle: float = 4.0) -> None:
    """Click through 'Okay' style modal popups.

    Popups render a frame or two after a screen change (the headless grid
    refreshes at G_FPS_CAP), so wait `settle` seconds for one to appear
    before deciding there is none.
    """
    deadline = time.time() + settle
    while True:
        if client.screen_has("Okay"):
            client.click_text("Okay")
            deadline = time.time() + 2.0
        elif time.time() >= deadline:
            return
        time.sleep(1)


def step(client: DFHackClient, label: str, pred, timeout: float = 30) -> None:
    """Click a button and verify its effect, re-dismissing modals as needed.

    Modal popups silently swallow clicks on buttons behind them, so a click
    without a verified effect is retried after another popup sweep.
    """
    deadline = time.monotonic() + timeout
    while True:
        dismiss_popups(client, settle=2.0)
        client.click_text(label)
        try:
            client.wait_for(f"effect of {label!r}", pred, timeout=8)
            return
        except DFError:
            if time.monotonic() >= deadline:
                raise DFError(f"clicking {label!r} had no effect")


def start_worldgen(client: DFHackClient) -> None:
    if get_screen_class(client) != "viewscreen_titlest":
        raise DFError("expected the title screen")
    step(client, "Create new world",
         lambda: get_screen_class(client) == "viewscreen_new_regionst")
    step(client, "Detailed mode",
         lambda: client.screen_has("New parameter set"))

    # Select the preset and pin all four seeds. has_* flags are required:
    # the seed strings alone are ignored (see dwarffortresswiki World_gen.txt).
    client.lua(f"""
        local scr = dfhack.gui.getCurViewscreen()
        local idx
        for i, p in ipairs(scr.worldgen_presets) do
            if p.title == "{PRESET_TITLE}" then idx = i - 1 end
        end
        assert(idx, "preset not found: {PRESET_TITLE}")
        scr.sel_param = idx
        local p = scr.worldgen_presets[idx]
        p.seed = "{SEEDS['seed']}";                   p.has_seed = true
        p.history_seed = "{SEEDS['history_seed']}";   p.has_history_seed = true
        p.name_seed = "{SEEDS['name_seed']}";         p.has_name_seed = true
        p.creature_seed = "{SEEDS['creature_seed']}"; p.has_creature_seed = true
        print("preset", idx, "seeded")
    """)
    step(client, "Create world",
         lambda: not client.screen_has("New parameter set"), timeout=60)
    log.info("world generation started")
    client.wait_for_text("Play now", timeout=1800)
    log.info("world generation finished")


def embark(client: DFHackClient) -> None:
    step(client, "Play now",
         lambda: get_screen_class(client) == "viewscreen_choose_game_typest",
         timeout=120)
    step(client, "Fortress",
         lambda: get_screen_class(client) == "viewscreen_choose_start_sitest",
         timeout=120)
    # Decline the "Quick start and short tutorial?" offer (we want our own
    # deterministic site, not the tutorial's), and sweep info popups. The
    # dialog can lag the screen change, so keep sweeping until the screen
    # has been free of both for a few seconds.
    deadline = time.monotonic() + 25
    quiet_since = time.monotonic()
    while time.monotonic() < deadline:
        clicked = False
        for label in ("Skip tutorial", "Okay"):
            if client.screen_has(label):
                try:
                    client.click_text(label, retry_for=0)
                    clicked = True
                except DFError:
                    pass  # vanished between the check and the click
        if clicked:
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since > 5:
            break
        time.sleep(1)

    enter_embark_placement(client)
    place_and_confirm(client)
    dismiss_popups(client)
    _click_first(client, ["Play now!"], lambda: _in_fort_mode(client),
                 timeout=600)
    log.info("embarked; fortress mode is live")


def enter_embark_placement(client: DFHackClient) -> None:
    """Click the bottom-bar Embark button to enter rectangle-placement mode.

    The site screen's bottom-bar buttons and the map itself are frame-polled
    (not fed through viewscreen feed()), so gui.simulateInput clicks are
    invisible to them. obs-mapclick pins the cursor + button state across
    real frames instead. The button label is located bottom-up because the
    instruction text above also contains the word "Embark".
    """
    coords = None
    for y, line in reversed(list(enumerate(client.screen_text()))):
        i = line.find("Embark")
        if i >= 0:
            coords = (i + 3, y)
            break
    if not coords:
        raise DFError("no Embark button on site screen")
    client.run_command("obs-mapclick", str(coords[0]), str(coords[1]))
    client.wait_for(
        "embark placement mode",
        lambda: client.lua(
            "print(dfhack.gui.getCurViewscreen().choosing_embark)"
        ).strip().endswith("true"),
        timeout=30)


def place_and_confirm(client: DFHackClient) -> None:
    """Center the view on the pinned site and click it.

    zoom_cent is in mid-level coordinates (region*16 + local tile). A click
    on a valid spot immediately opens the warnings/Confirm panel; on an
    invalid spot it just re-places the rectangle, which we detect and fail
    loudly on (the pinned site is known-valid for the pinned world).
    """
    cx, cy = EMBARK_MID_COORDS
    client.lua(f"local s=dfhack.gui.getCurViewscreen() "
               f"s.zoom_cent_x={cx} s.zoom_cent_y={cy} print('centered')")
    time.sleep(1)
    sw, sh = _window_size(client)
    pane = (sw // 2, sh // 2 - 2)   # where zoom_cent renders (150x66 -> 75,31)
    for attempt in range(3):
        client.run_command("obs-mapclick", str(pane[0]), str(pane[1]))
        try:
            client.wait_for("embark confirm panel",
                            lambda: client.screen_has("Confirm"), timeout=15)
            break
        except DFError:
            if attempt == 2:
                raise DFError(
                    "embark click did not open the Confirm panel; site "
                    "tile may be invalid for this world:\n"
                    + "\n".join(client.screen_text()))
    client.click_text("Confirm")
    client.wait_for(
        "embark prep screen",
        lambda: get_screen_class(client) == "viewscreen_setupdwarfgamest",
        timeout=300)


def _window_size(client: DFHackClient) -> tuple[int, int]:
    out = client.lua("print(dfhack.screen.getWindowSize())")
    parts = [p for p in out.split() if p.strip().isdigit()]
    return int(parts[0]), int(parts[1])


def _click_first(client: DFHackClient, labels: list[str], pred,
                 timeout: float = 60) -> None:
    """Like step(), but tries several candidate button labels."""
    deadline = time.monotonic() + timeout
    while True:
        dismiss_popups(client, settle=2.0)
        clicked = False
        for label in labels:
            if client.screen_has(label):
                client.click_text(label)
                clicked = True
                break
        try:
            if clicked:
                client.wait_for(f"effect of {labels!r}", pred, timeout=10)
                return
        except DFError:
            pass
        if time.monotonic() >= deadline:
            screen = "\n".join(client.screen_text())
            raise DFError(f"no effective click among {labels}:\n{screen}")
        time.sleep(2)


def _in_fort_mode(client: DFHackClient) -> bool:
    out = client.lua(
        "print(dfhack.world.isFortressMode() and dfhack.isMapLoaded())")
    return out.strip() == "true"


def save_and_archive(client: DFHackClient, df_dir: Path) -> Path:
    client.set_paused(True)
    client.quicksave()
    # quicksave is asynchronous; the request flag clears when DF has saved.
    client.wait_for(
        "quicksave to complete",
        lambda: client.lua(
            "print(df.global.plotinfo.main.autosave_request)").strip() == "false",
        timeout=600, interval=2)
    time.sleep(5)
    folder = client.save_folder()
    src = df_dir / "save" / folder
    if not src.exists() or not any(src.iterdir()):
        raise DFError(f"save folder missing or empty after quicksave: {src}")
    client.stop()

    dest = REPO_ROOT / "saves" / "dwarfciv-start"
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(exist_ok=True)
    shutil.copytree(src, dest)
    log.info("pristine save archived at %s", dest)
    return dest


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--df-dir", default=str(REPO_ROOT / "df"))
    args = ap.parse_args()
    df_dir = Path(args.df_dir).resolve()

    if (df_dir / "save").exists() and any((df_dir / "save").iterdir()):
        sys.exit(f"{df_dir}/save is not empty; delete it first to regenerate "
                 "the world (this guard avoids clobbering an existing fort)")

    client = DFHackClient(df_dir)
    try:
        client.start()
        client.wait_for("title screen",
                        lambda: get_screen_class(client) == "viewscreen_titlest",
                        timeout=120)
        start_worldgen(client)
        embark(client)
        dest = save_and_archive(client, df_dir)
    except DFError:
        try:
            log.error("failure screen dump:\n%s",
                      "\n".join(client.screen_text()))
        except DFError:
            pass
        raise
    finally:
        client.stop()
    print(f"\nDone. Pristine start save: {dest}")


if __name__ == "__main__":
    main()

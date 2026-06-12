"""Generate the pinned DwarfCiv world and embark, fully unattended.

Produces df/save/region1 (the live save DF will keep playing) and an
archived pristine copy in saves/dwarfciv-start/ that harness.loop restores
at the start of every run.

World recipe (see README "The pinned world"):
  - preset: POCKET REGION (17x17, 5 civs, site cap 18, 250 max years)
  - seeds:  SEED=dwarfciv  HISTORY_SEED=dwarfciv-history
            NAME_SEED=dwarfciv-names  CREATURE_SEED=dwarfciv-creatures
  - embark: region tile (8,8), local rect (6,6)-(9,9) [4x4], default
    "Play now" loadout, warnings bypassed via warn_flags.GENERIC
    (the gui/embark-anywhere technique).

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
EMBARK_REGION = (8, 8)        # region tile on the 17x17 world map
EMBARK_RECT = (6, 6, 9, 9)    # local 4x4 rect within the 16x16 region tile


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
        for label in ("Abort", "Okay"):
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

    rx, ry = EMBARK_REGION
    x0, y0, x1, y1 = EMBARK_RECT
    # Position the embark and force the confirmation panel to appear.
    # Setting warn_flags.GENERIC makes the accept-embark panel show
    # unconditionally (technique from DFHack's gui/embark-anywhere).
    client.lua(f"""
        local scr = dfhack.gui.getCurViewscreen()
        scr.location.region_pos.x = {rx}
        scr.location.region_pos.y = {ry}
        scr.location.embark_pos_min.x = {x0}
        scr.location.embark_pos_min.y = {y0}
        scr.location.embark_pos_max.x = {x1}
        scr.location.embark_pos_max.y = {y1}
        scr.zoom_cent_x = {rx}
        scr.zoom_cent_y = {ry}
        scr.zoomed_in = true
        scr.choosing_embark = true
        scr.warn_flags.GENERIC = true
        print("embark site set")
    """)
    time.sleep(2)
    # The accept-embark warning panel's button is labeled "Confirm" in 53.x.
    _click_first(client, ["Embark anyway", "Embark!", "Confirm"],
                 lambda: get_screen_class(client) == "viewscreen_setupdwarfgamest",
                 timeout=300)
    dismiss_popups(client)
    _click_first(client, ["Play now"], lambda: _in_fort_mode(client),
                 timeout=600)
    log.info("embarked; fortress mode is live")


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
    time.sleep(5)
    folder = client.save_folder()
    src = df_dir / "save" / folder
    client.wait_for("save written", lambda: (src / "world.sav").exists() or
                    any(src.glob("*.sav")), timeout=300)
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

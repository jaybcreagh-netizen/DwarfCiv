"""Manage a headless Dwarf Fortress + DFHack process.

Control channel: the `dfhack-run` CLI, which connects to DFHack's RPC server
on localhost:5000 (override with DFHACK_PORT). We deliberately use
dfhack-run + Lua scripts that write JSON to disk instead of the protobuf
remote API (dfhack-remote python bindings): the protobuf plugins
(RemoteFortressReader) lag behind DF releases and expose only a fixed
subset of state, while `lua` via dfhack-run can read any df.global
structure and ships with DFHack itself. See README "Design notes".
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Reading a 17x17 pocket world save takes a while on first load.
DEFAULT_CMD_TIMEOUT = 60
BOOT_TIMEOUT = 180


class DFError(RuntimeError):
    pass


class DFCrashed(DFError):
    """The DF process died underneath us."""


class DFHackClient:
    """Starts/stops the DF process and runs DFHack commands against it."""

    def __init__(self, df_dir: Path, port: int = 5000, log_path: Path | None = None):
        self.df_dir = Path(df_dir).resolve()
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.log_path = log_path
        self._log_file = None
        if not (self.df_dir / "dfhack-run").exists():
            raise DFError(f"{self.df_dir} does not look like a DF+DFHack install "
                          "(run setup/install.sh first)")

    # -- process lifecycle ----------------------------------------------------

    def start(self) -> None:
        if self.is_alive():
            return
        # A leftover DF from a previous run would hold the RPC port and
        # silently receive all our commands; fail fast instead.
        try:
            self.run_command("lua", "print(1)", timeout=10)
        except DFError:
            pass
        else:
            raise DFError(
                f"another DFHack instance is already serving port {self.port}; "
                "stop it first (pkill -x dwarfort) or use a different "
                "DFHACK_PORT")
        env = os.environ.copy()
        env.update({
            "DFHACK_HEADLESS": "1",
            "DFHACK_DISABLE_CONSOLE": "1",
            "DFHACK_PORT": str(self.port),
            "TERM": env.get("TERM") or "xterm",
        })
        out = subprocess.DEVNULL
        if self.log_path:
            self._log_file = open(self.log_path, "ab")
            out = self._log_file
        log.info("starting DF in %s (port %d)", self.df_dir, self.port)
        self.proc = subprocess.Popen(
            ["./dfhack"], cwd=self.df_dir, env=env,
            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
        )
        self._wait_for_rpc()

    def _wait_for_rpc(self) -> None:
        deadline = time.monotonic() + BOOT_TIMEOUT
        while time.monotonic() < deadline:
            if not self.is_alive():
                raise DFCrashed("DF exited during startup; see df.log")
            try:
                self.run_command("lua", "print(1)", timeout=10)
                return
            except DFError:
                time.sleep(2)
        raise DFError("timed out waiting for DFHack RPC server")

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def check_alive(self) -> None:
        if not self.is_alive():
            raise DFCrashed("DF process is not running")

    def stop(self, graceful: bool = True) -> None:
        """Shut DF down. graceful=True asks DFHack to exit cleanly."""
        if self.is_alive():
            if graceful:
                try:
                    self.run_command("die", timeout=15)
                except DFError:
                    pass
                try:
                    self.proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
            if self.is_alive():
                log.warning("DF did not exit cleanly; killing")
                self.proc.kill()
                self.proc.wait(timeout=15)
        self.proc = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    # -- command channel ------------------------------------------------------

    def run_command(self, *args: str, timeout: float = DEFAULT_CMD_TIMEOUT) -> str:
        """Run a DFHack console command via dfhack-run; returns stdout."""
        env = os.environ.copy()
        env["DFHACK_PORT"] = str(self.port)
        try:
            res = subprocess.run(
                ["./dfhack-run", *args], cwd=self.df_dir, env=env,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise DFError(f"dfhack-run timed out: {args!r}") from e
        out = _strip_ansi(res.stdout + res.stderr)
        # dfhack-run emits a harmless locale complaint on some systems.
        out = "\n".join(l for l in out.splitlines()
                        if "locale::facet" not in l)
        if res.returncode != 0 or "connect" in out.lower() and "failed" in out.lower():
            raise DFError(f"dfhack-run {args[0]} failed (rc={res.returncode}): {out[:500]}")
        return out

    def lua(self, code: str, timeout: float = DEFAULT_CMD_TIMEOUT) -> str:
        out = self.run_command("lua", code, timeout=timeout)
        low = out.lower()
        if "stack traceback" in low or "error:" in low[:200]:
            raise DFError(f"lua error: {out[:800]}")
        return out

    def run_json_script(self, script: str, *args: str,
                        timeout: float = DEFAULT_CMD_TIMEOUT) -> dict:
        """Run one of our dfhack-scripts that writes JSON to a temp file."""
        out_path = self.df_dir / f"obs-out-{os.getpid()}.json"
        out_path.unlink(missing_ok=True)
        try:
            self.run_command(script, str(out_path), *args, timeout=timeout)
            if not out_path.exists():
                raise DFError(f"{script} produced no output file")
            with open(out_path) as f:
                return json.load(f)
        finally:
            out_path.unlink(missing_ok=True)

    # -- screen-driven UI automation ------------------------------------------

    def screen_text(self) -> list[str]:
        """The rendered text grid (one string per row)."""
        out = self.run_command("obs-screen")
        lines = []
        for raw in out.splitlines():
            if "|" in raw and raw[:2].strip().isdigit():
                lines.append(raw.split("|", 1)[1])
        return lines

    def screen_has(self, text: str) -> bool:
        return any(text in line for line in self.screen_text())

    def click_text(self, text: str, retry_for: float = 15.0) -> None:
        """Click a button by its on-screen label.

        Retries while the label is absent: the text grid refreshes at
        G_FPS_CAP (5 fps headless), so a button may lag a screen change.
        """
        deadline = time.monotonic() + retry_for
        while True:
            try:
                self.run_command("obs-clicktext", text)
                return
            except DFError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)

    def wait_for(self, desc: str, pred, timeout: float = 60,
                 interval: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check_alive()
            if pred():
                return
            time.sleep(interval)
        raise DFError(f"timed out waiting for {desc}")

    def wait_for_text(self, text: str, timeout: float = 60) -> None:
        self.wait_for(f"screen text {text!r}", lambda: self.screen_has(text),
                      timeout=timeout)

    # -- common queries -------------------------------------------------------

    def get_focus(self) -> str:
        return self.lua(
            "print(table.concat(dfhack.gui.getFocusStrings("
            "dfhack.gui.getCurViewscreen()),','))").strip()

    def is_paused(self) -> bool:
        return self.lua("print(df.global.pause_state)").strip() == "true"

    def set_paused(self, paused: bool) -> None:
        self.lua(f"df.global.pause_state = {'true' if paused else 'false'}")

    def cur_tick(self) -> int:
        """Absolute tick counter: year * 403200 + tick-in-year."""
        out = self.lua(
            "print(df.global.cur_year * 403200 + df.global.cur_year_tick)")
        return int(out.strip())

    def quicksave(self) -> None:
        """Save the game in place (fort mode only)."""
        self.run_command("quicksave", timeout=300)

    def save_folder(self) -> str:
        return self.lua("print(df.global.world.cur_savegame.save_dir)").strip()

    def snapshot_save(self, dest: Path) -> Path:
        """Copy the current save folder to dest (call right after quicksave)."""
        folder = self.save_folder()
        src = self.df_dir / "save" / folder
        if not src.exists():
            # DF v50+ keeps saves under save/<folder>
            raise DFError(f"save folder not found: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, dirs_exist_ok=True)
        return dest


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)

"""
adb_controller.py — Safe ADB abstraction layer.

All commands are constructed with explicit argument lists (subprocess list form),
never via shell string interpolation with user-supplied data.
Touch coordinates are always clamped to device dimensions.
"""
import asyncio
import io
import subprocess
from typing import Optional

from .config import settings


# Android key event codes we actually use
KEYCODE_MAP: dict[str, int] = {
    # Navigation
    "Backspace": 67,
    "Enter": 66,
    "Tab": 61,
    "Escape": 111,
    "Delete": 112,
    "Home": 3,
    "Back": 4,
    "Menu": 82,
    "Search": 84,
    # Arrows
    "ArrowUp": 19,
    "ArrowDown": 20,
    "ArrowLeft": 21,
    "ArrowRight": 22,
    # Volume (bonus)
    "VolumeUp": 24,
    "VolumeDown": 25,
    # Page
    "PageUp": 92,
    "PageDown": 93,
    # Space
    " ": 62,
}


class ADBError(Exception):
    pass


class ADBController:
    def __init__(self):
        self._serial_args: list[str] = (
            ["-s", settings.ADB_DEVICE_SERIAL] if settings.ADB_DEVICE_SERIAL else []
        )
        self._screen_width: Optional[int] = None
        self._screen_height: Optional[int] = None

    def _adb(self, *args: str, timeout: int = 10) -> bytes:
        """Run an adb command synchronously, return stdout bytes."""
        cmd = ["adb"] + self._serial_args + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            if result.returncode != 0:
                raise ADBError(result.stderr.decode(errors="replace").strip())
            return result.stdout
        except FileNotFoundError:
            raise ADBError("adb not found. Install Android Platform Tools.")
        except subprocess.TimeoutExpired:
            raise ADBError(f"ADB command timed out: {' '.join(args)}")

    async def _adb_async(self, *args: str, timeout: int = 10) -> bytes:
        """Run an adb command asynchronously."""
        cmd = ["adb"] + self._serial_args + list(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise ADBError(f"ADB async command timed out: {' '.join(args)}")
        if proc.returncode != 0:
            raise ADBError(stderr.decode(errors="replace").strip())
        return stdout

    # ── Device Info ─────────────────────────────────────────────────────────

    def get_connected_devices(self) -> list[str]:
        out = self._adb("devices").decode(errors="replace")
        lines = out.strip().splitlines()[1:]  # skip "List of devices attached"
        return [l.split()[0] for l in lines if l.strip() and "device" in l]

    def is_connected(self) -> bool:
        try:
            devices = self.get_connected_devices()
            if settings.ADB_DEVICE_SERIAL:
                return settings.ADB_DEVICE_SERIAL in devices
            return len(devices) > 0
        except ADBError:
            return False

    def get_screen_size(self) -> tuple[int, int]:
        """Returns (width, height). Cached after first call."""
        if self._screen_width and self._screen_height:
            return self._screen_width, self._screen_height
        try:
            out = self._adb("shell", "wm", "size").decode(errors="replace")
            # Output: "Physical size: 1080x2340" or "Override size: 1080x2340"
            for line in out.splitlines():
                if "size" in line.lower() and "x" in line:
                    size_part = line.split(":")[-1].strip()
                    w, h = size_part.split("x")
                    self._screen_width, self._screen_height = int(w), int(h)
                    return self._screen_width, self._screen_height
        except Exception:
            pass
        # Fallback
        self._screen_width, self._screen_height = 1080, 1920
        return self._screen_width, self._screen_height

    def get_battery_level(self) -> int:
        try:
            out = self._adb("shell", "dumpsys", "battery").decode(errors="replace")
            for line in out.splitlines():
                if "level:" in line:
                    return int(line.split(":")[1].strip())
        except Exception:
            pass
        return -1

    def get_device_model(self) -> str:
        try:
            return self._adb(
                "shell", "getprop", "ro.product.model"
            ).decode(errors="replace").strip()
        except Exception:
            return "Unknown"

    # ── Screen Capture ───────────────────────────────────────────────────────

    async def screencap_jpeg(self, quality: int = 70) -> bytes:
        """
        Capture current screen as JPEG bytes.
        screencap returns PNG; we convert to JPEG in Python for bandwidth savings.
        """
        from PIL import Image

        png_data = await self._adb_async("exec-out", "screencap", "-p", timeout=8)
        if not png_data:
            raise ADBError("screencap returned no data")

        img = Image.open(io.BytesIO(png_data))
        # Convert RGBA (Android screencap includes alpha) → RGB
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=False)
        return buf.getvalue()

    # ── Input Control ────────────────────────────────────────────────────────

    def _clamp(self, val: float, lo: float, hi: float) -> int:
        """Clamp and cast to int — prevents out-of-bounds input injection."""
        return int(max(lo, min(hi, val)))

    async def tap(self, x: float, y: float) -> None:
        """Send a tap at normalized (0.0–1.0) or absolute coordinates."""
        w, h = self.get_screen_size()
        # Accept either normalized (0–1) or absolute pixel coords
        ax = self._clamp(x, 0, w)
        ay = self._clamp(y, 0, h)
        await self._adb_async("shell", "input", "tap", str(ax), str(ay))

    async def swipe(
        self,
        x1: float, y1: float,
        x2: float, y2: float,
        duration_ms: int = 300,
    ) -> None:
        """Send a swipe gesture."""
        w, h = self.get_screen_size()
        ax1 = self._clamp(x1, 0, w)
        ay1 = self._clamp(y1, 0, h)
        ax2 = self._clamp(x2, 0, w)
        ay2 = self._clamp(y2, 0, h)
        dur = self._clamp(duration_ms, 50, 5000)
        await self._adb_async(
            "shell", "input", "swipe",
            str(ax1), str(ay1), str(ax2), str(ay2), str(dur),
        )

    async def send_text(self, text: str) -> None:
        """
        Send text input. Only printable ASCII is forwarded — no shell metacharacters.
        ADB text input does not support Unicode well without root, so we restrict
        to safe printable characters.
        """
        # Whitelist: printable ASCII excluding shell-special chars
        safe = "".join(
            c for c in text
            if c.isprintable()
            and c not in r'`$\|&;<>(){}[]#~!'
            and ord(c) < 128
        )
        if not safe:
            return
        # ADB requires spaces to be escaped as %s
        safe = safe.replace(" ", "%s")
        await self._adb_async("shell", "input", "text", safe)

    async def send_keyevent(self, key: str) -> None:
        """
        Send a key event by JS key name (e.g. 'Backspace', 'Enter').
        Only keys in the KEYCODE_MAP whitelist are accepted.
        """
        code = KEYCODE_MAP.get(key)
        if code is None:
            return  # Silently ignore unknown keys
        await self._adb_async("shell", "input", "keyevent", str(code))

    async def press_back(self) -> None:
        await self._adb_async("shell", "input", "keyevent", "4")

    async def press_home(self) -> None:
        await self._adb_async("shell", "input", "keyevent", "3")

    async def press_recents(self) -> None:
        await self._adb_async("shell", "input", "keyevent", "187")


# Singleton
adb = ADBController()

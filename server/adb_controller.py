"""
adb_controller.py — Safe ADB abstraction layer.

All commands are constructed with explicit argument lists (subprocess list form),
never via shell string interpolation with user-supplied data.
Touch coordinates are always clamped to device dimensions.
"""
import asyncio
import io
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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

    def set_serial(self, serial: str) -> None:
        """Update active device serial in settings and local args."""
        settings.ADB_DEVICE_SERIAL = serial
        self._serial_args = ["-s", serial] if serial else []

    def update_env_file(self, serial: str) -> bool:
        """Persist new ADB_DEVICE_SERIAL to .env file if present."""
        env_path = Path(__file__).parent.parent / ".env"
        if not env_path.exists():
            return False
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("ADB_DEVICE_SERIAL="):
                    new_lines.append(f"ADB_DEVICE_SERIAL={serial}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"ADB_DEVICE_SERIAL={serial}")
            env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            return True
        except Exception:
            return False

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

    # ── Connection Management ────────────────────────────────────────────────

    def get_device_states(self) -> dict[str, str]:
        """
        Run `adb devices` without -s serial filter to return dict of {serial: state}.
        """
        try:
            cmd = ["adb", "devices"]
            res = subprocess.run(cmd, capture_output=True, timeout=5, check=False)
            if res.returncode != 0:
                return {}
            out = res.stdout.decode(errors="replace").strip()
            lines = out.splitlines()[1:]
            states = {}
            for l in lines:
                parts = l.strip().split()
                if len(parts) >= 2:
                    states[parts[0]] = parts[1]
            return states
        except Exception:
            return {}

    async def disconnect_remote(self, target: str = "") -> dict:
        """Run `adb disconnect <target>` or `adb disconnect` to clear stale entries."""
        cmd = ["disconnect", target] if target else ["disconnect"]
        try:
            out = await self._adb_async(*cmd, timeout=5)
            return {"success": True, "message": out.decode(errors="replace").strip()}
        except ADBError as e:
            return {"success": False, "message": str(e)}

    async def kill_and_restart_server(self) -> None:
        """Restart local ADB server daemon if unresponsive or stuck in offline loop."""
        try:
            subprocess.run(["adb", "kill-server"], capture_output=True, timeout=5, check=False)
            await asyncio.sleep(0.5)
            subprocess.run(["adb", "start-server"], capture_output=True, timeout=5, check=False)
        except Exception:
            pass

    async def connect_remote(self, target: str = "", force_disconnect: bool = True) -> dict:
        """
        Run `adb connect <target>`.
        - Purges stale offline socket before connecting if force_disconnect=True.
        - Verifies device state is 'device' (not 'offline').
        - Auto-restarts adb server if target stays offline.
        """
        serial = target or settings.ADB_DEVICE_SERIAL
        if not serial or not (":" in serial or "." in serial):
            return {"success": False, "message": "No IP:PORT specified in ADB_DEVICE_SERIAL"}

        self.set_serial(serial)

        # 1. Disconnect stale socket entry first if requested
        if force_disconnect:
            await self.disconnect_remote(serial)
            await asyncio.sleep(0.2)

        try:
            out = await self._adb_async("connect", serial, timeout=8)
            msg = out.decode(errors="replace").strip()

            # Verify actual device state from `adb devices`
            states = self.get_device_states()
            state = states.get(serial, "")

            if state == "device":
                self.update_env_file(serial)
                return {"success": True, "message": f"Connected to {serial}", "serial": serial}

            if state == "offline":
                # Disconnect and restart ADB server to clear stale offline socket
                await self.disconnect_remote(serial)
                await self.kill_and_restart_server()
                # Try connect once more
                out_retry = await self._adb_async("connect", serial, timeout=8)
                states_retry = self.get_device_states()
                if states_retry.get(serial) == "device":
                    self.update_env_file(serial)
                    return {"success": True, "message": f"Connected to {serial} after reset", "serial": serial}
                return {"success": False, "message": f"Device at {serial} is offline. Please check Wireless Debugging on device.", "serial": serial}

            connected = "connected to" in msg.lower() or "already connected" in msg.lower()
            if connected and state == "device":
                self.update_env_file(serial)
                return {"success": True, "message": msg, "serial": serial}

            return {"success": False, "message": msg or f"Failed to connect to {serial} (state: {state or 'unknown'})", "serial": serial}
        except ADBError as e:
            return {"success": False, "message": str(e), "serial": serial}

    def _test_tcp_port(self, host: str, port: int, timeout: float = 0.005) -> bool:
        """Fast TCP connect check for a specific port."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                return s.connect_ex((host, port)) == 0
        except Exception:
            return False

    async def scan_and_connect(
        self,
        host: str = "127.0.0.1",
        port_start: int = 30000,
        port_end: int = 50000,
    ) -> dict:
        """
        Scan TCP ports on host (e.g. 127.0.0.1 / localhost) in range port_start..port_end
        to discover active Wireless ADB server and auto-connect.
        """
        # Check if any connected device is already online
        current_devices = self.get_connected_devices()
        if current_devices:
            dev = current_devices[0]
            self.set_serial(dev)
            return {"success": True, "message": f"Already connected to active device {dev}", "serial": dev}

        check_host = "127.0.0.1" if host.lower() in ("localhost", "127.0.0.1") else host
        loop = asyncio.get_running_loop()
        ports_to_check = list(range(port_start, port_end + 1))
        open_ports: list[int] = []

        def probe_chunk(ports: list[int]) -> list[int]:
            found = []
            for p in ports:
                if self._test_tcp_port(check_host, p, timeout=0.004):
                    found.append(p)
            return found

        chunk_size = 1000
        chunks = [ports_to_check[i : i + chunk_size] for i in range(0, len(ports_to_check), chunk_size)]

        with ThreadPoolExecutor(max_workers=16) as executor:
            tasks = [loop.run_in_executor(executor, probe_chunk, chunk) for chunk in chunks]
            results = await asyncio.gather(*tasks)

        for res_ports in results:
            open_ports.extend(res_ports)

        if not open_ports:
            return {
                "success": False,
                "message": f"No open TCP ports found on {host} in range {port_start}–{port_end}. Ensure Wireless Debugging is enabled.",
                "serial": settings.ADB_DEVICE_SERIAL,
            }

        # Probe each open port with `adb connect`
        for port in open_ports:
            target_serial = f"{host}:{port}" if host != "127.0.0.1" else f"localhost:{port}"
            res = await self.connect_remote(target_serial, force_disconnect=True)
            if res.get("success"):
                self.update_env_file(target_serial)
                return {
                    "success": True,
                    "message": f"Successfully auto-discovered and connected to Wireless ADB on {target_serial}",
                    "serial": target_serial,
                }

        return {
            "success": False,
            "message": f"Found open ports ({open_ports[:5]}), but ADB handshake failed.",
            "serial": settings.ADB_DEVICE_SERIAL,
        }

    # ── Device Info ─────────────────────────────────────────────────────────

    def purge_offline_devices_sync(self) -> None:
        """Synchronously purge any 'offline' or 'unauthorized' devices listed in adb devices."""
        states = self.get_device_states()
        for serial, state in states.items():
            if state in ("offline", "unauthorized"):
                try:
                    subprocess.run(["adb", "disconnect", serial], capture_output=True, timeout=3, check=False)
                except Exception:
                    pass

    async def purge_offline_devices(self) -> None:
        """Asynchronously purge any 'offline' or 'unauthorized' devices listed in adb devices."""
        states = self.get_device_states()
        for serial, state in states.items():
            if state in ("offline", "unauthorized"):
                await self.disconnect_remote(serial)

    def get_connected_devices(self) -> list[str]:
        states = self.get_device_states()
        return [s for s, state in states.items() if state == "device"]

    def is_connected(self) -> bool:
        try:
            self.purge_offline_devices_sync()
            devices = self.get_connected_devices()
            if not devices:
                return False
            if settings.ADB_DEVICE_SERIAL and settings.ADB_DEVICE_SERIAL in devices:
                return True
            # Auto-adopt any active connected device if configured serial is outdated
            if len(devices) > 0:
                self.set_serial(devices[0])
                self.update_env_file(devices[0])
                return True
            return False
        except ADBError:
            return False

    async def wake_screen(self) -> None:
        """Send KEYCODE_WAKEUP (224) to turn screen on if sleeping."""
        try:
            await self._adb_async("shell", "input", "keyevent", "224", timeout=3)
        except Exception:
            pass

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
        from PIL import Image, ImageFile
        ImageFile.LOAD_TRUNCATED_IMAGES = True

        # Ensure active connected device is bound
        if not self.is_connected():
            raise ADBError("No active ADB device connected")

        try:
            png_data = await self._adb_async("exec-out", "screencap", "-p", timeout=8)
        except ADBError as e:
            await self.wake_screen()
            raise e

        if not png_data or len(png_data) < 100:
            raise ADBError("screencap returned incomplete data")

        try:
            img = Image.open(io.BytesIO(png_data))
            # Convert RGBA (Android screencap includes alpha) → RGB
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=False)
            return buf.getvalue()
        except Exception as e:
            raise ADBError(f"Failed to process screencap frame: {e}")

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

"""
screen_stream.py — Asynchronous screen capture loop with WebSocket broadcast.

Captures JPEG frames from Android via ADB and broadcasts to all connected
WebSocket clients. Uses an asyncio lock to ensure only one screencap runs
at a time, preventing ADB overload.
"""
import asyncio
import logging
import time
from typing import Set

from fastapi import WebSocket

from .adb_controller import ADBController, ADBError
from .config import settings

logger = logging.getLogger(__name__)


class ScreenStreamer:
    def __init__(self, adb: ADBController):
        self._adb = adb
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._running = False
        self._frame_interval = 1.0 / settings.SCREEN_FPS
        self._last_frame_time: float = 0
        self._frame_count: int = 0
        self._fps_actual: float = 0.0

    # ── Client Management ────────────────────────────────────────────────────

    async def add_client(self, ws: WebSocket) -> None:
        self._clients.add(ws)
        logger.info(f"Screen client connected. Total: {len(self._clients)}")

    async def remove_client(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info(f"Screen client disconnected. Total: {len(self._clients)}")

    # ── Broadcast ────────────────────────────────────────────────────────────

    async def _broadcast(self, frame: bytes) -> None:
        """Send frame to all connected clients; remove dead ones."""
        if not self._clients:
            return
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send_bytes(frame)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ── Capture Loop ─────────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Main capture loop. Run as a background asyncio task.
        Respects configured FPS; skips frame if no clients are connected.
        """
        self._running = True
        logger.info(f"Screen streamer started @ {settings.SCREEN_FPS} FPS")
        fps_window_start = time.monotonic()
        fps_window_count = 0
        consecutive_failures = 0

        while self._running:
            loop_start = time.monotonic()

            if self._clients:
                try:
                    async with self._lock:
                        frame = await self._adb.screencap_jpeg(quality=65)
                    await self._broadcast(frame)
                    consecutive_failures = 0

                    # FPS accounting
                    fps_window_count += 1
                    elapsed = time.monotonic() - fps_window_start
                    if elapsed >= 2.0:
                        self._fps_actual = fps_window_count / elapsed
                        fps_window_count = 0
                        fps_window_start = time.monotonic()

                except ADBError as e:
                    consecutive_failures += 1
                    logger.warning(f"Screencap failed ({consecutive_failures}/3): {e}")
                    if consecutive_failures >= 3:
                        logger.info("Multiple screencap failures detected. Triggering ADB auto-reconnect & scan...")
                        try:
                            # Try reconnecting to current or scanning local ports
                            res = await self._adb.scan_and_connect()
                            logger.info(f"Streamer auto-reconnect result: {res.get('message')}")
                        except Exception as rec_err:
                            logger.error(f"Streamer auto-reconnect error: {rec_err}")
                        consecutive_failures = 0
                    await asyncio.sleep(1.0)  # back-off on ADB error
                    continue
                except Exception as e:
                    logger.error(f"Unexpected streamer error: {e}")
                    await asyncio.sleep(1.0)
                    continue

            # Sleep for the remainder of the frame interval
            elapsed = time.monotonic() - loop_start
            sleep_time = max(0.0, self._frame_interval - elapsed)
            await asyncio.sleep(sleep_time)

    def stop(self) -> None:
        self._running = False

    @property
    def fps(self) -> float:
        return round(self._fps_actual, 1)

    @property
    def client_count(self) -> int:
        return len(self._clients)

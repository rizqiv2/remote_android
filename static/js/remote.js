/**
 * remote.js — Android remote control interface.
 *
 * Handles:
 *  - WebSocket connection for live screen frames
 *  - Canvas rendering of JPEG frames
 *  - Touch/swipe input mapping from canvas → Android coordinates
 *  - Keyboard input → ADB key events / text
 *  - CSRF token management for all control requests
 *  - Connection status, FPS counter, device info polling
 *
 * Security:
 *  - All API calls include X-CSRF-Token header (read from cookie)
 *  - No innerHTML with user data — all dynamic text via textContent
 *  - WebSocket authenticated with short-lived JWT
 */

'use strict';

// ── Constants ─────────────────────────────────────────────────────────────────
const STATUS_POLL_MS = 5000;    // How often to poll /api/status
const RECONNECT_DELAY_MS = 3000; // WS reconnect delay after failure

// ── DOM refs ──────────────────────────────────────────────────────────────────
const canvas            = document.getElementById('screenCanvas');
const ctx               = canvas.getContext('2d');
const overlayConnecting = document.getElementById('overlayConnecting');
const overlayDiscon     = document.getElementById('overlayDisconnected');
const statusDot         = document.getElementById('statusDot');
const statusLabel       = document.getElementById('statusLabel');
const deviceName        = document.getElementById('deviceName');
const deviceMeta        = document.getElementById('deviceMeta');
const fpsCounter        = document.getElementById('fpsCounter');
const battLevel         = document.getElementById('battLevel');
const battIcon          = document.getElementById('battIcon');
const connResolution    = document.getElementById('connResolution');
const connViewers       = document.getElementById('connViewers');
const btnBack           = document.getElementById('btnBack');
const btnHome           = document.getElementById('btnHome');
const btnRecents        = document.getElementById('btnRecents');
const btnVolUp          = document.getElementById('btnVolUp');
const btnVolDown        = document.getElementById('btnVolDown');
const btnSend           = document.getElementById('btnSend');
const textInput         = document.getElementById('textInput');
const btnLogout         = document.getElementById('btnLogout');
const btnFullscreen     = document.getElementById('btnFullscreen');
const btnReconnect      = document.getElementById('btnReconnect');
const btnAdbReconnect   = document.getElementById('btnAdbReconnect');
const touchRipple       = document.getElementById('touchRipple');

// ── State ─────────────────────────────────────────────────────────────────────
let ws = null;
let wsToken = null;
let deviceWidth = 1080;
let deviceHeight = 1920;
let frameCount = 0;
let lastFpsTime = performance.now();
let reconnectTimer = null;

// Swipe tracking
let pointerDown = false;
let pointerStartX = 0, pointerStartY = 0;
let pointerLastX = 0, pointerLastY = 0;
let pointerStartTime = 0;
const SWIPE_THRESHOLD = 10; // px on canvas

// ── CSRF Helper ───────────────────────────────────────────────────────────────

function getCsrfToken() {
  for (const part of document.cookie.split(';')) {
    const t = part.trim();
    if (t.startsWith('csrf_token=')) {
      return decodeURIComponent(t.slice('csrf_token='.length));
    }
  }
  return '';
}

// ── API Helpers ───────────────────────────────────────────────────────────────

async function apiPost(path, body = {}) {
  const csrf = getCsrfToken();
  try {
    const r = await fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf,
      },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
    if (r.status === 401) {
      // Session expired — redirect to login
      window.location.href = '/login';
    }
    return r;
  } catch (e) {
    console.warn('API error:', path, e);
    return null;
  }
}

// ── Coordinate Mapping ────────────────────────────────────────────────────────

/**
 * Map a canvas pixel coordinate to Android device coordinates.
 * The canvas may be scaled by CSS; we use canvas.getBoundingClientRect().
 */
function canvasToDevice(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = deviceWidth / rect.width;
  const scaleY = deviceHeight / rect.height;
  return {
    x: Math.round((clientX - rect.left) * scaleX),
    y: Math.round((clientY - rect.top) * scaleY),
  };
}

// ── Touch Ripple ──────────────────────────────────────────────────────────────

function showRipple(clientX, clientY) {
  touchRipple.style.left = clientX + 'px';
  touchRipple.style.top = clientY + 'px';
  touchRipple.classList.remove('hidden');
  // Reset animation
  touchRipple.style.animation = 'none';
  touchRipple.offsetHeight; // reflow
  touchRipple.style.animation = '';
  setTimeout(() => touchRipple.classList.add('hidden'), 450);
}

// ── Canvas Input ──────────────────────────────────────────────────────────────

canvas.addEventListener('pointerdown', (e) => {
  e.preventDefault();
  pointerDown = true;
  pointerStartX = pointerLastX = e.clientX;
  pointerStartY = pointerLastY = e.clientY;
  pointerStartTime = performance.now();
  canvas.setPointerCapture(e.pointerId);
});

canvas.addEventListener('pointermove', (e) => {
  if (!pointerDown) return;
  pointerLastX = e.clientX;
  pointerLastY = e.clientY;
});

canvas.addEventListener('pointerup', async (e) => {
  if (!pointerDown) return;
  pointerDown = false;

  const dx = e.clientX - pointerStartX;
  const dy = e.clientY - pointerStartY;
  const dist = Math.sqrt(dx * dx + dy * dy);

  showRipple(pointerStartX, pointerStartY);

  if (dist < SWIPE_THRESHOLD) {
    // Tap
    const { x, y } = canvasToDevice(e.clientX, e.clientY);
    await apiPost('/api/control/tap', { x, y });
  } else {
    // Swipe
    const start = canvasToDevice(pointerStartX, pointerStartY);
    const end   = canvasToDevice(e.clientX, e.clientY);
    const dur   = Math.min(5000, Math.max(50, performance.now() - pointerStartTime));
    await apiPost('/api/control/swipe', {
      x1: start.x, y1: start.y,
      x2: end.x,   y2: end.y,
      duration_ms: Math.round(dur),
    });
  }
});

canvas.addEventListener('pointercancel', () => { pointerDown = false; });

// ── Keyboard Input ────────────────────────────────────────────────────────────

const KEY_EVENT_KEYS = new Set([
  'Backspace', 'Enter', 'Tab', 'Escape', 'Delete',
  'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
  'PageUp', 'PageDown', ' ',
]);

document.addEventListener('keydown', async (e) => {
  // Don't intercept when text input is focused
  if (document.activeElement === textInput) return;

  if (e.key === 'F11') {
    e.preventDefault();
    toggleFullscreen();
    return;
  }

  if (e.key === 'Escape' && !document.fullscreenElement) {
    // Send back button
    await apiPost('/api/control/back');
    return;
  }

  if (KEY_EVENT_KEYS.has(e.key)) {
    e.preventDefault();
    await apiPost('/api/control/keyevent', { key: e.key });
    return;
  }

  // Printable characters: batch into text
  if (e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
    e.preventDefault();
    await apiPost('/api/control/text', { text: e.key });
  }
});

// ── Navigation Buttons ────────────────────────────────────────────────────────

btnBack.addEventListener('click',    () => apiPost('/api/control/back'));
btnHome.addEventListener('click',    () => apiPost('/api/control/home'));
btnRecents.addEventListener('click', () => apiPost('/api/control/recents'));
btnVolUp.addEventListener('click',   () => apiPost('/api/control/keyevent', { key: 'VolumeUp' }));
btnVolDown.addEventListener('click', () => apiPost('/api/control/keyevent', { key: 'VolumeDown' }));

// ── Text Send ─────────────────────────────────────────────────────────────────

async function sendText() {
  const text = textInput.value.trim();
  if (!text) return;
  textInput.value = '';
  await apiPost('/api/control/text', { text });
}

btnSend.addEventListener('click', sendText);
textInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); sendText(); }
});

// ── Logout ────────────────────────────────────────────────────────────────────

btnLogout.addEventListener('click', async () => {
  await apiPost('/api/logout');
  window.location.href = '/login';
});

// ── Fullscreen ────────────────────────────────────────────────────────────────

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
    document.body.classList.add('fullscreen');
  } else {
    document.exitFullscreen().catch(() => {});
    document.body.classList.remove('fullscreen');
  }
}

btnFullscreen.addEventListener('click', toggleFullscreen);

document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement) {
    document.body.classList.remove('fullscreen');
  }
});

// ── WebSocket Screen Stream ───────────────────────────────────────────────────

function setStatus(state, label) {
  statusDot.className = 'status-dot ' + state;
  statusLabel.textContent = label;
}

function showOverlay(which) {
  overlayConnecting.classList.toggle('hidden', which !== 'connecting');
  overlayDiscon.classList.toggle('hidden', which !== 'disconnected');
}

async function getWsToken() {
  try {
    const r = await fetch('/api/ws-token', { credentials: 'same-origin' });
    if (!r.ok) {
      if (r.status === 401) window.location.href = '/login';
      return null;
    }
    const data = await r.json();
    return data.token;
  } catch {
    return null;
  }
}

async function connectWs() {
  setStatus('loading', 'Connecting…');
  showOverlay('connecting');

  wsToken = await getWsToken();
  if (!wsToken) {
    setStatus('error', 'Auth failed');
    showOverlay('disconnected');
    return;
  }

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/ws/screen?token=${encodeURIComponent(wsToken)}`;

  ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    setStatus('live', 'Live');
    showOverlay(null);
    canvas.focus();
    schedulePing();
  };

  ws.onmessage = (event) => {
    if (typeof event.data === 'string') return; // pong / text
    renderFrame(event.data);
  };

  ws.onerror = () => {
    setStatus('error', 'Error');
  };

  ws.onclose = () => {
    setStatus('error', 'Disconnected');
    showOverlay('disconnected');
    ws = null;
    // Auto-reconnect
    reconnectTimer = setTimeout(connectWs, RECONNECT_DELAY_MS);
  };
}

// ── Ping / Keepalive ──────────────────────────────────────────────────────────

function schedulePing() {
  setTimeout(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send('ping');
      schedulePing();
    }
  }, 20000);
}

// ── Frame Rendering ───────────────────────────────────────────────────────────

async function renderFrame(arrayBuffer) {
  try {
    const blob = new Blob([arrayBuffer], { type: 'image/jpeg' });
    const bitmap = await createImageBitmap(blob);

    // Resize canvas to match first frame dimensions
    if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      deviceWidth = bitmap.width;
      deviceHeight = bitmap.height;
    }

    ctx.drawImage(bitmap, 0, 0);
    bitmap.close();

    // FPS counter
    frameCount++;
    const now = performance.now();
    if (now - lastFpsTime >= 1000) {
      const fps = Math.round(frameCount / ((now - lastFpsTime) / 1000));
      fpsCounter.textContent = fps;
      frameCount = 0;
      lastFpsTime = now;
    }
  } catch (e) {
    console.warn('Frame render error:', e);
  }
}

// ── Reconnect Button ──────────────────────────────────────────────────────────

btnReconnect.addEventListener('click', () => {
  clearTimeout(reconnectTimer);
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  connectWs();
});

if (btnAdbReconnect) {
  btnAdbReconnect.addEventListener('click', async () => {
    btnAdbReconnect.textContent = '⏳ Connecting...';
    btnAdbReconnect.disabled = true;
    try {
      const res = await apiPost('/api/adb/reconnect');
      const data = await res?.json();
      btnAdbReconnect.textContent = data?.success ? '✅ Connected' : '❌ Failed';
    } catch {
      btnAdbReconnect.textContent = '❌ Error';
    }
    setTimeout(() => {
      btnAdbReconnect.textContent = '🔄 Reconnect ADB';
      btnAdbReconnect.disabled = false;
    }, 3000);
  });
}

// ── Status Polling ─────────────────────────────────────────────────────────────

async function pollStatus() {
  try {
    const r = await fetch('/api/status', { credentials: 'same-origin' });
    if (r.status === 401) { window.location.href = '/login'; return; }
    if (!r.ok) return;

    const data = await r.json();

    if (data.connected) {
      // Safe text rendering — never innerHTML
      deviceName.textContent = data.model || 'Android Device';
      deviceMeta.textContent = `${data.screen_width}×${data.screen_height}`;
      battLevel.textContent = data.battery >= 0 ? data.battery : '--';
      battIcon.textContent = data.battery >= 20 ? '🔋' : '🪫';
      connResolution.textContent = `${data.screen_width}×${data.screen_height}`;
      connViewers.textContent = data.viewers;
      deviceWidth = data.screen_width || deviceWidth;
      deviceHeight = data.screen_height || deviceHeight;
    } else {
      deviceName.textContent = 'No device';
      deviceMeta.textContent = 'Check ADB connection';
    }
  } catch {
    // Network error — silently ignore, will retry
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

connectWs();
pollStatus();
setInterval(pollStatus, STATUS_POLL_MS);

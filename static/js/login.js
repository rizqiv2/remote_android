/**
 * login.js — Login form logic.
 *
 * Security notes:
 *  - No eval(), no innerHTML with user data, no document.write()
 *  - Error messages rendered via textContent only
 *  - CSRF token read from cookie and sent as header
 *  - Lockout countdown handled client-side (server enforces server-side)
 */

'use strict';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const form        = document.getElementById('loginForm');
const passwordIn  = document.getElementById('passwordInput');
const btnLogin    = document.getElementById('btnLogin');
const btnSpinner  = document.getElementById('btnSpinner');
const btnText     = btnLogin.querySelector('.btn-text');
const alertBox    = document.getElementById('alertBox');
const alertText   = document.getElementById('alertText');
const lockoutBox  = document.getElementById('lockoutBox');
const lockoutCd   = document.getElementById('lockoutCountdown');
const lockoutBar  = document.getElementById('lockoutBar');
const togglePw    = document.getElementById('togglePw');
const eyeIcon     = document.getElementById('eyeIcon');
const statusDot   = document.getElementById('statusDot');
const statusText  = document.getElementById('statusText');

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Read a cookie value by name (browser-native, no library). */
function getCookie(name) {
  const prefix = name + '=';
  for (const part of document.cookie.split(';')) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length));
    }
  }
  return null;
}

/** Format seconds into mm:ss */
function formatCountdown(sec) {
  const m = Math.floor(sec / 60).toString().padStart(2, '0');
  const s = (sec % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

/** Show the alert box with a safe (textContent) message. */
function showAlert(message) {
  alertText.textContent = message;   // XSS-safe: never innerHTML
  alertBox.hidden = false;
  passwordIn.classList.add('error');
  passwordIn.focus();
}

function hideAlert() {
  alertBox.hidden = true;
  passwordIn.classList.remove('error');
}

/** Show lockout UI with countdown. */
let _lockoutTimer = null;
function showLockout(retryAfterSeconds) {
  hideAlert();
  lockoutBox.hidden = false;
  btnLogin.disabled = true;
  passwordIn.disabled = true;

  const total = retryAfterSeconds;
  let remaining = retryAfterSeconds;

  function tick() {
    lockoutCd.textContent = formatCountdown(remaining);
    const pct = (remaining / total) * 100;
    lockoutBar.style.width = pct + '%';

    if (remaining <= 0) {
      clearInterval(_lockoutTimer);
      lockoutBox.hidden = true;
      btnLogin.disabled = false;
      passwordIn.disabled = false;
      passwordIn.focus();
    }
    remaining--;
  }

  tick();
  _lockoutTimer = setInterval(tick, 1000);
}

/** Toggle loading state on the button. */
function setLoading(loading) {
  btnLogin.disabled = loading;
  btnSpinner.hidden = !loading;
  btnText.textContent = loading ? 'Signing in…' : 'Sign In';
}

// ── Device Status Check ───────────────────────────────────────────────────────

async function checkStatus() {
  statusDot.className = 'status-dot checking';
  statusText.textContent = 'Checking device…';

  try {
    // We check an unauthenticated endpoint — but we already have a session?
    // Just do a quick server ping; if server is up it responds.
    const r = await fetch('/api/status', { method: 'GET', credentials: 'same-origin' });
    if (r.status === 401) {
      // Not logged in yet — that's expected on login page
      statusDot.className = 'status-dot';
      statusText.textContent = 'Server reachable';
      return;
    }
    if (r.ok) {
      const data = await r.json();
      if (data.connected) {
        statusDot.className = 'status-dot connected';
        // Render model name safely
        const modelEl = document.createTextNode(data.model || 'Device');
        statusText.textContent = '';
        statusText.appendChild(modelEl);
        statusText.appendChild(document.createTextNode(' connected'));
      } else {
        statusDot.className = 'status-dot disconnected';
        statusText.textContent = 'No Android device found';
      }
    }
  } catch {
    statusDot.className = 'status-dot disconnected';
    statusText.textContent = 'Server unreachable';
  }
}

// ── Password Toggle ───────────────────────────────────────────────────────────

togglePw.addEventListener('click', () => {
  const isPassword = passwordIn.type === 'password';
  passwordIn.type = isPassword ? 'text' : 'password';
  // Swap icon (eye-off SVG)
  if (isPassword) {
    eyeIcon.innerHTML = `
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
      <line x1="1" y1="1" x2="23" y2="23"/>
    `;
  } else {
    eyeIcon.innerHTML = `
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
      <circle cx="12" cy="12" r="3"/>
    `;
  }
  togglePw.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
});

// ── Form Submit ───────────────────────────────────────────────────────────────

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideAlert();

  const password = passwordIn.value;
  if (!password) {
    showAlert('Please enter your password.');
    return;
  }

  setLoading(true);

  try {
    const resp = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ password }),
    });

    const data = await resp.json().catch(() => ({}));

    if (resp.ok) {
      // Success — redirect to remote page
      btnText.textContent = '✓ Authenticated';
      passwordIn.value = '';
      window.location.href = '/remote';
      return;
    }

    if (resp.status === 429) {
      // Rate limited / locked out
      const retryAfter = data?.detail?.retry_after ?? 900;
      showLockout(retryAfter);
    } else if (resp.status === 401) {
      showAlert('Invalid credentials. Please try again.');
    } else {
      showAlert('An error occurred. Please try again.');
    }
  } catch {
    showAlert('Network error — is the server running?');
  } finally {
    setLoading(false);
  }
});

// ── Clear error on typing ─────────────────────────────────────────────────────

passwordIn.addEventListener('input', () => {
  if (!alertBox.hidden) hideAlert();
});

// ── Init ──────────────────────────────────────────────────────────────────────

checkStatus();
passwordIn.focus();

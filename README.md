# Android Remote Control 📱

A self-hosted web app to view and control your Android device from any browser — with a hardened, secure login page.

## Features

- 🖥️ **Live screen mirroring** — real-time JPEG stream over WebSocket
- 👆 **Touch & swipe control** — click/drag on the canvas
- ⌨️ **Keyboard input** — type directly, or use text box to send text
- 🔒 **Hardened login** — bcrypt, rate limiting, CSRF, CSP headers, HttpOnly JWT
- 📱 **Responsive** — works from phone browser too
- 🌐 **Tunnel-ready** — works behind ngrok, Cloudflare Tunnel, etc.

---

## Requirements

| Tool | Install |
|---|---|
| Python 3.10+ | `sudo apt install python3` |
| ADB | `sudo apt install adb` |
| Android (USB Debug enabled) | Developer Options → USB Debugging |

---

## Quick Start

### 1. Run setup
```bash
bash setup.sh
```
This will:
- Create a Python virtual environment
- Install all dependencies
- Generate a JWT secret
- Ask you to set a password (stored as bcrypt hash in `.env`)

### 2. Connect your Android
```bash
adb devices   # USB connection
# or
adb connect 192.168.x.x:5555   # Wi-Fi (enable wireless ADB in Dev Options)
```

### 3. Start the server
```bash
source .venv/bin/activate
python -m server.main
```

### 4. Open in browser
```
http://localhost:8080
```

---

## Internet Access (via tunnel)

### ngrok
```bash
ngrok http 8080
```

### Cloudflare Tunnel
```bash
cloudflared tunnel --url http://localhost:8080
```

> ⚠️ When using HTTPS via tunnel, change `secure=False` → `secure=True` in `server/main.py` for the cookie settings.

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `PASSWORD_HASH` | — | bcrypt hash of your password (set by setup.sh) |
| `JWT_SECRET` | — | Random hex string for signing tokens |
| `JWT_EXPIRE_HOURS` | `8` | Session lifetime |
| `RATE_LIMIT_MAX_ATTEMPTS` | `5` | Failed logins before lockout |
| `RATE_LIMIT_LOCKOUT_SECONDS` | `900` | Lockout duration (15 min) |
| `ADB_DEVICE_SERIAL` | _(first device)_ | Specific device serial |
| `SCREEN_FPS` | `10` | Screen capture FPS (1–30) |
| `SERVER_HOST` | `127.0.0.1` | Bind address |
| `SERVER_PORT` | `8080` | Port |

---

## Security Architecture

| Layer | Measure |
|---|---|
| **Brute force** | 5 attempts/IP → 15-min lockout (server-side, sliding window) |
| **Password** | bcrypt cost=12, constant-time comparison |
| **Session** | JWT in `HttpOnly; SameSite=Strict` cookie — JS cannot read it |
| **CSRF** | Double-submit cookie pattern — every POST validates header vs cookie |
| **XSS** | Strict CSP (`script-src 'self'`), all DOM writes via `textContent` |
| **Clickjacking** | `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` |
| **ADB injection** | All commands use subprocess list form, coords clamped, text whitelisted |
| **Error messages** | Generic — no difference between "wrong user" vs "wrong password" |

---

## Controls Reference

| Action | How |
|---|---|
| Tap | Click on screen |
| Swipe | Click and drag |
| Type text | Focus canvas, type keys OR use Text box in sidebar |
| Back | `Esc` key or ← button |
| Home | 🏠 button |
| Recents | ⊞ button |
| Fullscreen | `F11` or ⛶ button |
| Volume | 🔊/🔇 buttons in sidebar |

---

## Project Structure

```
remote_android/
├── server/
│   ├── main.py            # FastAPI app, routes, middleware
│   ├── auth.py            # bcrypt, JWT, CSRF, rate limiter
│   ├── adb_controller.py  # Safe ADB abstraction
│   ├── screen_stream.py   # Async screen capture + WS broadcast
│   └── config.py          # Settings from .env
├── static/
│   ├── login.html / remote.html
│   ├── css/ (login.css, remote.css)
│   └── js/  (login.js, remote.js)
├── requirements.txt
├── setup.sh
└── .env.example
```

# Android Remote Control 📱⚡

A self-hosted, secure web application to view and remotely control your Android phone or tablet from any web browser worldwide — featuring live screen mirroring, touch & swipe control, keyboard input, security audit logs, and battery protection.

---

## 🌟 Key Features

- 🖥️ **Live Screen Streaming** — Low-latency JPEG stream over WebSocket (canvas rendering).
- 👆 **Full Control** — Tap, drag/swipe, back, home, recents, volume, and text input.
- 📱 **2 Deployment Modes**:
  - **PC Host Mode:** Linux PC runs the server and controls phone over USB/Wi-Fi.
  - **Standalone Android Mode:** Runs **100% inside your Android Phone/Tablet** via Termux — *no PC needed!*
- 🔒 **Hardened Security**:
  - **bcrypt** password hashing (cost 12, constant-time compare).
  - **IP Rate Limiting** (5 failed attempts → 15-minute lockout).
  - **HttpOnly JWT Session Cookies** (JS cannot steal session tokens).
  - **CSRF Double-Submit Tokens** on all state-changing actions.
  - **Strict CSP Headers** & XSS protection.
- 🛡️ **Security Audit Logs** — Immutable, paginated activity log recording logins, taps, swipes, and commands (auto-rotated to max 10,000 entries so storage never fills up).
- 🔋 **Battery Health Protection** — Setup guide for 24/7 plugged-in server tablets (Samsung 80% charge limit).

---

## 🛠️ Deployment Mode 1: Standalone on Android (Termux — No PC Needed)

This mode turns your Android phone or tablet into an **unattended 24/7 standalone server**.

### Step 1: Install Termux & Tailscale
1. Install **Termux** (from F-Droid or GitHub).
2. Install **Tailscale** on your Android phone and enable **Always-On VPN**:
   - *Android Settings → Connections → More Connection Settings → VPN → Tailscale ⚙️ → Always-on VPN: ON*.

### Step 2: Install Packages in Termux
Open Termux on your phone and run:

```bash
# 1. Update and install Python, ADB, and dependencies
apt update && apt install -y python android-tools libjpeg-turbo zlib git termux-api

# 2. Clone or copy project
cd ~
git clone https://github.com/rizqiv2/remote_android.git
cd remote_android

# 3. Create virtual environment & install requirements
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Set your login password
python set_password.py
```

### Step 3: Enable Auto-Wireless ADB via MacroDroid
Since Android turns off Wireless Debugging after a reboot, use **MacroDroid** (or Shizuku) to re-enable it automatically:

1. In **MacroDroid**, create a Macro:
   - **Trigger:** Device Boot (or Direct Boot)
   - **Action:** System Setting → Global → `adb_wifi_enabled` = `1`
2. Grant permission once via Termux:
   ```bash
   adb shell pm grant com.arlosoft.macrodroid android.permission.WRITE_SECURE_SETTINGS
   ```

### Step 4: Setup 100% Unattended Auto-Start Script
To make your server auto-detect the dynamic Wireless Debugging port and launch on reboot:

```bash
mkdir -p ~/.termux/boot

cat << 'EOF' > ~/.termux/boot/autostart.sh
#!/data/data/com.termux/files/usr/bin/bash

# 1. Prevent CPU sleep
termux-wake-lock

# 2. Wait 8s for network and Wireless ADB to initialize
sleep 8

# 3. Auto-find real ADB port by verifying actual ADB handshake
PORT=$(python3 -c "
import socket, subprocess
def find_adb():
    for p in range(30000, 50000):
        s = socket.socket()
        s.settimeout(0.005)
        if s.connect_ex(('127.0.0.1', p)) == 0:
            s.close()
            r = subprocess.run(['adb', 'connect', f'localhost:{p}'], capture_output=True, text=True)
            if 'connected' in r.stdout.lower() or 'already connected' in r.stdout.lower():
                devs = subprocess.run(['adb', 'devices'], capture_output=True, text=True).stdout
                if f'localhost:{p}' in devs and 'offline' not in devs:
                    return p
        else:
            s.close()
    return ''
print(find_adb())
")

if [ -n "$PORT" ]; then
    echo "✅ Verified ADB port: $PORT"
    cd ~/remote_android
    sed -i "s/ADB_DEVICE_SERIAL=.*/ADB_DEVICE_SERIAL=localhost:$PORT/" .env
else
    adb connect localhost:5555
fi

# 4. Start Python Remote Control Server in background
cd ~/remote_android
nohup python -m server.main > ~/server.log 2>&1 &
EOF

chmod +x ~/.termux/boot/autostart.sh
```

---

## 🖥️ Deployment Mode 2: Hosted on Linux PC

If you prefer running the server on your Linux PC:

```bash
# 1. Run setup script
bash setup.sh

# 2. Connect Android via USB or Wireless ADB
adb devices

# 3. Start server
source .venv/bin/activate
python -m server.main
```

---

## 🌐 Accessing the Remote Control Web UI

Open any web browser on your PC, laptop, or phone connected to your Tailscale network:

👉 **`http://<your-phone-tailscale-ip>:8080`**

- Log in with your password.
- Click the **📄 Audit Logs** icon in the top header to view paginated security logs.
- Click **🔄 Reconnect ADB** in the side panel if ADB ever needs manual reconnection.

---

## 🔋 24/7 Battery Protection Setup (Samsung Devices)

To prevent battery swelling or degradation when plugged into power 24/7:

1. **Via Samsung Settings:**
   - *Settings → Battery → Protect Battery / Battery Protection → Set to **Maximum (80% Limit)**.*
2. **Via ADB Command:**
   ```bash
   adb shell settings put global protect_battery 1
   ```

---

## 🛡️ Security Architecture

| Security Measure | Implementation |
|---|---|
| **Brute Force Protection** | 5 failed attempts per IP → 15-minute server-side lockout |
| **Password Storage** | bcrypt (cost factor 12), constant-time string comparison |
| **Session Security** | JWT signed tokens stored in `HttpOnly; SameSite=Strict` cookies |
| **CSRF Defense** | Double-submit token on all state-changing endpoints |
| **XSS Defense** | Strict CSP (`script-src 'self'`), DOM nodes updated via `.textContent` only |
| **Audit Logs** | Paginated activity logger storing up to 10,000 rotating JSONL entries |
| **ADB Command Injection** | Subprocess argument lists (`shell=False`), coordinate clamping |

---

## 📂 Project Structure

```
remote_android/
├── server/
│   ├── main.py            # FastAPI application & API endpoints
│   ├── auth.py            # bcrypt, JWT, CSRF, and rate limiting
│   ├── audit_logger.py    # Rotating audit logger (max 10,000 entries)
│   ├── adb_controller.py  # Safe ADB abstraction layer
│   ├── screen_stream.py   # Async screen capture & WebSocket streamer
│   └── config.py          # Environment settings loader
├── static/
│   ├── login.html / remote.html
│   ├── css/ (login.css, remote.css)
│   └── js/  (login.js, remote.js)
├── requirements.txt
├── setup.sh
├── set_password.py
└── .env.example
```

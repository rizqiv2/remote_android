# 📱 100% Unattended Android Remote Access Guide

This guide explains how to configure your Android phone or tablet as a **24/7 standalone, unattended remote desktop server**. 

Once configured, the tablet can reboot or power-cycle indefinitely, and it will automatically reconnect to Tailscale, auto-discover Wireless ADB, launch the Python server, and protect its battery — **with zero human interaction**.

---

## 🏗️ Architecture Overview

```
[ Unattended Android Device ]
  ├── Tailscale VPN (Always-On Mode)
  ├── MacroDroid (Auto-enables Wireless Debugging on boot)
  ├── Termux + Termux:Boot (Auto-scans ADB port & starts server)
  └── Samsung Battery Protection (Capped at 80% charge)
        ▲
        │  (Direct encrypted remote access from anywhere)
        │
[ Your Web Browser / PC ]  --->  http://<phone-tailscale-ip>:8080
```

---

## 📋 Step-by-Step Unattended Setup

### Step 1: Set Tailscale to Always-On VPN
To guarantee your device is accessible over the internet immediately after boot:

1. Open Android **Settings** → **Connections** (or **Network & Internet**).
2. Tap **More Connection Settings** → **VPN**.
3. Tap the **Gear ⚙️** next to **Tailscale**.
4. Turn **ON** **"Always-on VPN"**.

---

### Step 2: Configure Android Screen & Direct Boot Settings

Because Android blocks background apps until the first screen unlock:

1. Open **Settings** → **Lock Screen**.
2. Set **Screen Lock Type** to **Swipe** or **None** *(so the tablet finishes booting automatically without waiting for a PIN)*.
3. Open **Settings** → **Developer Options**:
   - Turn **ON** **"Stay awake"** *(Screen will not sleep while charging)*.

---

### Step 3: Configure MacroDroid to Auto-Enable Wireless Debugging on Boot

Android turns off Wireless Debugging after a reboot. MacroDroid will turn it back ON automatically:

1. Install **MacroDroid** on the Android device.
2. Create a new Macro:
   - **Trigger:** Device Boot *(or Direct Boot)*
   - **Action:** System Setting → Global → `adb_wifi_enabled` = `1`
3. Grant MacroDroid permission once via Termux:
   ```bash
   adb shell pm grant com.arlosoft.macrodroid android.permission.WRITE_SECURE_SETTINGS
   ```

---

### Step 4: Setup Termux Auto-Port Scanner & Boot Script

Android assigns a dynamic random port to Wireless Debugging on boot. This script automatically finds the active port, completes the ADB handshake, and starts the server.

1. Open **Termux** and install dependencies:
   ```bash
   apt update && apt install -y python android-tools libjpeg-turbo zlib git termux-api
   ```
2. Set up the unattended boot script:

```bash
mkdir -p ~/.termux/boot

cat << 'EOF' > ~/.termux/boot/autostart.sh
#!/data/data/com.termux/files/usr/bin/bash

# 1. Keep CPU awake
termux-wake-lock

# 2. Wait for Android network & MacroDroid to initialize
sleep 8

# 3. Find the REAL ADB port by verifying actual ADB handshake
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

### Step 5: Prevent Battery Degradation (24/7 Power Protection)

To prevent battery swelling when plugged into a charger 24/7:

1. **Samsung Settings:** Go to *Settings → Battery → Protect Battery / Battery Protection → Select **Maximum (80% Limit)**.*
2. **Or via ADB command:**
   ```bash
   adb shell settings put global protect_battery 1
   ```

---

### Step 6: Disable Battery Optimization for Termux & MacroDroid

Ensure Android never kills Termux or MacroDroid in the background:

1. **Settings** → **Apps** → **Termux** → **Battery** → Select **Unrestricted**.
2. **Settings** → **Apps** → **MacroDroid** → **Battery** → Select **Unrestricted**.
3. **Settings** → **Battery** → **Background usage limits** → **Never sleeping apps** → Add **Termux**, **MacroDroid**, and **Tailscale**.

---

## 🔍 Verification & Testing

To test unattended operation:

1. Reboot your Android phone/tablet.
2. Wait 15–20 seconds without touching the tablet.
3. Open any browser on your PC or laptop connected to Tailscale and go to:
   `http://<phone-tailscale-ip>:8080`
4. Enter your password. You will see your live Android screen! 🎉

---

## 🛠️ Troubleshooting Checklist

| Issue | Cause | Fix |
|---|---|---|
| `Refused to connect` on browser | Server not running or Tailscale VPN disconnected | Ensure Tailscale is set to *Always-On VPN* in Android settings. |
| `Device offline` or `Connection refused` in logs | Wireless Debugging port changed on boot | MacroDroid must auto-enable Wireless Debugging on boot, and `autostart.sh` will auto-scan the port. |
| Server stops working after 1 hour | Android battery saver killed Termux | Set Termux & Tailscale battery settings to *Unrestricted*. |

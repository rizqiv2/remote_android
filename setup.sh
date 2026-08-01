#!/usr/bin/env bash
# setup.sh — First-time setup for Android Remote Control
# Run: bash setup.sh

set -e
cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     Android Remote Control — Setup       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Check deps ────────────────────────────────────────────────────────────────

check_cmd() {
  if ! command -v "$1" &>/dev/null; then
    echo "❌  $1 not found. Please install it first."
    echo "    $2"
    exit 1
  fi
  echo "✅  $1 found"
}

check_cmd python3 "sudo apt install python3"
check_cmd pip3    "sudo apt install python3-pip"
check_cmd adb     "sudo apt install adb   OR   install Android Platform Tools"

echo ""

# ── Python venv ───────────────────────────────────────────────────────────────

if [ ! -d ".venv" ]; then
  echo "📦  Creating Python virtual environment…"
  python3 -m venv .venv
fi

source .venv/bin/activate
echo "📦  Installing Python dependencies…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "✅  Dependencies installed"
echo ""

# ── .env setup ────────────────────────────────────────────────────────────────

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "📄  Created .env from template"
fi

# Generate JWT secret if missing
if grep -q "your_jwt_secret_here" .env; then
  JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  # In-place replacement (works on Linux)
  sed -i "s/your_jwt_secret_here/$JWT_SECRET/" .env
  echo "🔑  Generated JWT secret"
fi

# Set password
if grep -q "your_bcrypt_hash_here" .env; then
  echo ""
  echo "🔐  Set your login password:"
  echo -n "    Enter password: "
  read -rs PASSWORD
  echo ""
  HASH=$(python3 -c "import bcrypt; print(bcrypt.hashpw(b'$PASSWORD', bcrypt.gensalt(12)).decode())")
  # Escape hash for sed (bcrypt hash contains $ and /)
  ESCAPED_HASH=$(printf '%s\n' "$HASH" | sed 's/[[\.*^$()+?{|]/\\&/g; s|/|\\/|g; s/\$/\\\$/g')
  sed -i "s|your_bcrypt_hash_here|$ESCAPED_HASH|" .env
  echo "✅  Password set"
fi

echo ""

# ── ADB check ─────────────────────────────────────────────────────────────────

echo "📱  Checking ADB devices…"
adb devices -l 2>/dev/null || true
echo ""

echo "╔══════════════════════════════════════════╗"
echo "║  Setup complete! Start the server with:  ║"
echo "║                                          ║"
echo "║    source .venv/bin/activate             ║"
echo "║    python -m server.main                 ║"
echo "║                                          ║"
echo "║  Then open: http://localhost:8080        ║"
echo "╚══════════════════════════════════════════╝"
echo ""

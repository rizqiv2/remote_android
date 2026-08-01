#!/usr/bin/env python3
"""
Set or update the password in .env cleanly without shell escaping issues.
"""
import getpass
import re
from pathlib import Path
import bcrypt

env_path = Path(__file__).parent / ".env"
env_example = Path(__file__).parent / ".env.example"

if not env_path.exists():
    if env_example.exists():
        env_path.write_text(env_example.read_text())
    else:
        print("❌ .env.example not found!")
        exit(1)

password = getpass.getpass("🔐 Enter new password: ")
confirm = getpass.getpass("🔐 Confirm new password: ")

if password != confirm:
    print("❌ Passwords do not match!")
    exit(1)

if not password:
    print("❌ Password cannot be empty!")
    exit(1)

# Generate bcrypt hash
hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(12)).decode("utf-8")

content = env_path.read_text()
if "PASSWORD_HASH=" in content:
    content = re.sub(r"PASSWORD_HASH=.*", f"PASSWORD_HASH={hashed}", content)
else:
    content += f"\nPASSWORD_HASH={hashed}\n"

env_path.write_text(content)
print("✅ Password updated successfully in .env!")

#!/usr/bin/env python3
"""Break-glass recovery: reset (or create) the DKNS Super Admin account's
password directly against your production Postgres database.

Use this only when you're locked out of the DKNS account -- this bypasses
the app entirely, so run it yourself, from your own machine, against your
own database. Never paste your POSTGRES_URL into a chat, ticket, or commit.

Usage:
    1. Get your production connection string from Vercel -> your project ->
       Storage -> your Postgres database -> ".env.local" tab (POSTGRES_URL).
    2. pip install -r requirements.txt   (only needed once, for psycopg2/werkzeug)
    3. POSTGRES_URL="postgres://..." python scripts/reset_super_admin.py
    4. Enter a new password when prompted (input is hidden, not echoed).

The script normalizes the username to exactly "DKNS", sets role=Admin and
is_active=1, and hashes the password the same way the app does
(werkzeug's generate_password_hash), so it works immediately with the
existing login form -- no other code changes needed.
"""
import getpass
import os
import sys

import psycopg2
from werkzeug.security import generate_password_hash

SUPER_ADMIN_USERNAME = "DKNS"


def main():
    db_url = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("Set POSTGRES_URL (or DATABASE_URL) to your production connection string first.")
        print('Example: POSTGRES_URL="postgres://..." python scripts/reset_super_admin.py')
        sys.exit(1)

    password = getpass.getpass(f"New password for {SUPER_ADMIN_USERNAME}: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match. Nothing was changed.")
        sys.exit(1)
    if len(password) < 8:
        print("Use at least 8 characters. Nothing was changed.")
        sys.exit(1)

    password_hash = generate_password_hash(password)

    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s)", (SUPER_ADMIN_USERNAME,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE users SET username = %s, password_hash = %s, role = 'Admin', is_active = 1 WHERE id = %s",
                    (SUPER_ADMIN_USERNAME, password_hash, row[0]),
                )
                print(f"Updated the existing {SUPER_ADMIN_USERNAME} account (id={row[0]}).")
            else:
                cur.execute(
                    "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, %s, 'Admin', 1)",
                    (SUPER_ADMIN_USERNAME, password_hash),
                )
                print(f"Created the {SUPER_ADMIN_USERNAME} account (it didn't exist yet).")
    finally:
        conn.close()

    print(f"Done. Log in at your site with username '{SUPER_ADMIN_USERNAME}' and the password you just set.")


if __name__ == "__main__":
    main()

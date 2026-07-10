# DKNS Tea Lands

A web app for managing tea estate employees, attendance, harvest collection, and payroll.

## Features

- **Employees** — digital profiles (personal + employment details), auto-generated employee IDs, printable QR check-in badges
- **Attendance** — check-in/out, breaks, status, verification method; auto-computed work hours
- **Work & Harvest** — daily task assignments with a harvest target; weighing entries automatically update output and productivity (`Actual_Output += Net_Weight`, `Productivity = Actual_Output / Target`); non-harvest tasks (Fertilizer/Pruning/Weeding/Spraying) can be marked Pending/In Progress/Completed manually. Each employee can have an over-target commission % — kg up to the target are paid at the normal rate/kg, and kg beyond the target earn that commission as a bonus on top of the rate (e.g. target 25kg, rate 50, 20% commission, 29kg actual → 25×50 + 4×50×1.20 = 1490)
- **Payroll** — harvest-based pay (`kg × rate/kg`) and time-based pay (`hours × hourly rate`) per employee over a date range, with CSV export. Salary advances can be recorded per employee/date and are shown as a separate Advance column with a computed Net Pay (`Total Pay − Advance`) — advances don't affect the Income page's cost figures, since they're a prepayment of the same pay, not an extra expense. Each employee has a downloadable PDF payslip for the selected period (earnings, harvest, advances, net pay). Employees can opt in to **EPF/ETF** (Sri Lanka statutory rates): 8% is deducted from the employee's pay (reduces Net Pay), while the employer separately contributes 12% EPF + 3% ETF — a real added cost to the estate that flows into the Income page's cost/profit calculation, but is never deducted from the employee
- **QR check-in** — each employee's badge encodes a URL; scanning it with any phone's camera (no app needed) opens a check-in page and logs attendance automatically
- **Login** — a single admin account gates every management page; the QR check-in and badge pages stay public so employees never need credentials to check in
- **Income & Profit** — set the tea selling price for any day (it changes daily); view Daily, Weekly (Mon–Sun), Monthly (calendar month), or a Custom date range. Income sums each day's harvest × *that day's* price (never a single price across a range), and flags any days in the range with no price set instead of guessing. Also shows Cost (employee pay + logged expenses), Profit/Loss, a per-employee income/profit breakdown, and a percentage-of-cost breakdown
- **Expenses** — occasional costs (Fertilizer, Transport, Fuel, Food, Others) logged per date, only added when they actually happen; they fold into that day's total cost and profit calculation

## Requirements

- Python 3.10+
- Dependencies in `requirements.txt` (Flask, qrcode, Pillow, reportlab)

## Setup

```bash
pip install -r requirements.txt
python app.py
```

The app creates `tea_estate.db` (SQLite) in this folder on first run — no separate database setup needed.

Visit `http://localhost:5000` on the machine running it. The first visit will prompt you to create the admin account (username + password) — there are no default/hardcoded credentials.

## Using it from other devices (phones, tablets)

The server binds to `0.0.0.0`, so other devices on the same WiFi/LAN can reach it:

1. Find the host machine's LAN IP (Windows: `ipconfig`, look for the IPv4 address on your WiFi/Ethernet adapter).
2. From another device on the same network, visit `http://<that-ip>:5000`.
3. **Windows Firewall**: the first time, Windows will likely prompt "Windows Defender Firewall has blocked some features of this app" — click **Allow access** (at least for Private networks), or other devices won't be able to connect.
4. Print employee QR badges *after* accessing the app via the LAN IP (not `localhost`) — the badge encodes whatever address you used to load the page, so it needs to be the address other phones can actually reach.

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Tests use a temporary SQLite database (never touches your real `tea_estate.db`) and cover auth, employee CRUD, attendance, the harvest weighing math from the spec, payroll calculations, and the QR check-in flow — including regression tests for bugs found during development (employee-number collisions after deletes, stale-edit conflicts, `None` leaking into form values).

## What's not built yet

These need real hardware/decisions before they can be implemented:

- **Face-recognition check-in** — needs a camera device and a face-matching service; QR check-in covers the same need today with zero extra hardware.
- **IoT smart scale integration** — weighing is currently manual entry (staff type in gross/tare weight). Once you have a scale, tell me how it outputs data (Bluetooth, WiFi/API, USB/serial) and it can be wired in to replace manual entry.

## Known limitations

- Single admin account, no roles/permissions — anyone who logs in can view/edit everything. Fine for one office admin; not meant for many staff with different access levels.
- Optimistic concurrency prevents two simultaneous edits from silently overwriting each other, but there's no true multi-user support (audit log, per-user attribution).
- Uses Flask's built-in development server (`debug=True`) — fine for a single estate's internal use, but not hardened for public internet exposure.
- Sessions use a hardcoded `app.secret_key` in `app.py` — fine for internal LAN use, but if you ever expose this beyond your local network, replace it with a real secret (e.g. from an environment variable).
- **Only run one `python app.py` at a time.** SQLite only allows one writer; if you start it twice (e.g. from two terminals) you'll get `database is locked` errors. Stop a running instance with Ctrl+C before starting another, rather than opening a second terminal on top of it.
- EPF/ETF percentages (`EPF_EMPLOYEE_PERCENT`, `EPF_EMPLOYER_PERCENT`, `ETF_EMPLOYER_PERCENT` in `utils.py`) are hardcoded at the current standard Sri Lanka statutory rates (8% / 12% / 3%). If the government changes these rates, update the constants in `utils.py` — they apply to every EPF/ETF-enabled employee automatically.

## Database connection handling

`database.get_connection()` caches one SQLite connection per request on Flask's `g`, and `app.py` registers `close_connection` via `app.teardown_appcontext()`. Flask guarantees teardown callbacks run even when a request raises an unhandled exception — so a crash can never leave a connection (and its lock) dangling. This replaced an earlier pattern of manually calling `conn.close()` at the end of each route, which silently leaked the connection (and the lock) whenever a route crashed before reaching that line.

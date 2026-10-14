# Zabbix Report Studio

A self-hosted reporting and monitoring portal for [Zabbix](https://www.zabbix.com/).
It connects to one or more Zabbix servers through the JSON-RPC API and turns raw
events into clean dashboards, filterable alarm reports, acknowledgement (ACK)
analysis and hardware inventory — with **Excel & PDF export** built in.

Runs as a local **web app** (`python app.py`) or as a **native desktop window**
(`desktop.py`, powered by pywebview). Data is stored locally in SQLite — no
external database or Docker required.

> The user interface is in **English** by default, with a **Turkish** option: use the
> EN / TR switch at the top of the sidebar (remembered per browser). Translations live
> in [`static/i18n.js`](static/i18n.js).

**▶ [Live demo](https://cemilcan0.github.io/zabbix-report-studio-demo/)** — the real
UI running in your browser on generated sample data (no server, no Zabbix). It is
a static build of the templates (see [`demo/`](demo)) served by GitHub Pages from
the separate public `zabbix-report-studio-demo` repo. To update it:

```bash
pip install jinja2
python demo/build.py ../zabbix-report-studio-demo
cd ../zabbix-report-studio-demo && git add -A && git commit -m "Update demo" && git push
```

---

## ✨ Features

- **Multi-server** — manage several Zabbix servers per user; passwords are stored
  encrypted (SECRET_KEY-derived, see [Security](#-security)) and HTTPS certificates
  are verified (can be turned off per server for a self-signed certificate). Switch
  the active server at any time; caches and stats are scoped per server.
- **Overview dashboard** — live KPIs computed straight from the API: currently
  active problems (with the same disabled/maintenance/dependency filtering the
  Zabbix Problems view uses), 30-day totals, severity distribution, top alarming
  hosts, monitored host count and average/median resolution time.
- **Alarm Report** — filter by date range, host groups and severity; day-by-day
  event fetch with an on-disk cache for fast re-runs; in-hours/after-hours (shift)
  analysis with configurable business hours, optional SLA availability, and **Excel + PDF** export.
  **Compare with previous period** fetches the equally long period right before
  the selected range and shows what changed (totals, resolution time, per
  severity, and the hosts that moved the most).
- **Scheduled reports** — the alarm report built daily, weekly or monthly and
  e-mailed: an HTML summary compared with the previous period, the same summary as
  a PDF and the full alarm list as an Excel attachment, in English or Turkish. See
  [Scheduled reports](#-scheduled-reports).
- **ACK Report** — how quickly problems were acknowledged, who acknowledged them,
  and who was on call at the moment of each alarm.
- **On-call (nöbet) roster** — a repeating N-day cycle with configurable day/night
  shift hours, managed from the UI.
- **Inventory** — pulls serial numbers / device models per category (SAN SW,
  Servers, Storage, Tape) directly from Zabbix; loads automatically on open.
- **Trigger search** — list every trigger whose name contains the text you type,
  with its status and hosts.
- **User management** with three roles (admin, user, read-only viewer) and a
  **cache viewer** for the local event cache.

---

## 🧱 Tech stack

Flask · Flask-SQLAlchemy · Flask-Login · SQLite · Waitress (production WSGI) ·
pywebview (desktop) · openpyxl (Excel) · fpdf2 (PDF in scheduled reports) ·
Chart.js / jsPDF (client-side charts & PDF).

---

## 🚀 Quick start

### Requirements
- Python 3.10+
- A reachable Zabbix server (5.x / 6.x / 7.x) with an API user. The session token is
  sent the way the server version expects it (request body before 6.4, `Authorization:
  Bearer` header from 6.4 on, which 7.2+ requires).

### Run as a web app

```bash
pip install -r requirements.txt
python app.py
```

Open <http://localhost:5050> and sign in with the default account:

```
username: admin
password: Admin123!
```

You are asked to choose a new password straight away — nothing else is reachable
until you do.

> On Windows you can also just double-click **`run.bat`**.

After logging in, go to **Servers** and add your Zabbix server (URL, username,
password). If you have exactly one server it is activated automatically.

### Run as a desktop app (Windows)

```bash
pip install -r requirements.txt
python desktop.py          # or double-click run-app.bat
```

This launches the portal in its own window instead of a browser tab. For a silent
launch (no console) use **`Zabbix Report Studio.vbs`**.

### Run with Docker

```bash
docker build -t zabbix-report-studio .
docker run -d --name zrs -p 127.0.0.1:5050:5050 -v zrs-data:/data zabbix-report-studio
```

The databases and `secret.key` live in the `/data` volume. The container listens
on all interfaces (`ZRS_HOST=0.0.0.0`), so publish the port on `127.0.0.1` as
above or put a TLS reverse proxy in front of it — see [Security](#-security).

---

## 👥 Roles

| Role | Can do |
|------|--------|
| **admin** | Everything: users, the on-call roster, SMTP settings, own servers and schedules. |
| **user** | Own Zabbix servers and scheduled reports, all reports. |
| **viewer** | Read-only: sees the reports of the servers added by admins; cannot add or change servers, schedules, the cache or anything else. |

Passwords typed by an admin (new user, password reset) are temporary: the user
must replace them at the next sign-in. Everyone can change their own password
from the key icon at the bottom of the sidebar.

---

## 📬 Scheduled reports

1. As admin, open **Scheduled Reports** and fill in the **SMTP settings**
   (host, port, STARTTLS / SSL / none, optional username and password, sender
   address). *Send test e-mail* checks them.
2. Create a schedule: server, frequency (daily / weekly on a weekday / monthly on a
   day of the month), hour, host groups, severities, recipients and report language.
3. A daily report covers the previous day, a weekly one the previous 7 days, a
   monthly one the previous calendar month. *Send now* sends it immediately.

Business hours (default: weekdays 08:00–18:00) are set by an admin on the
**On-call Roster** page and apply to all reports, including events already cached.

Reports are sent by a background thread of the running application (checked every
30 seconds, local time). A report that was due while the application was not
running is sent once at the next start. The report uses the same day-by-day cache
as the Alarm Report page.

---

## ⚙️ Configuration

No configuration file is required — you can add Zabbix servers entirely from the UI.

Optionally, create a `.env` (copy from [`.env.example`](.env.example)) to set a
stable secret key and/or seed a first server on initial startup:

```env
SECRET_KEY=a-long-random-string
ZABBIX_URL=http://your-zabbix-host/api_jsonrpc.php
ZABBIX_USER=your-zabbix-user
ZABBIX_PASSWORD=your-zabbix-password
```

- **`SECRET_KEY`** encrypts the Zabbix passwords stored in the database. If it is
  not set, a random key is generated once and saved to `secret.key`. Keep this
  value stable — if it changes, previously stored server passwords can no longer
  be decrypted and must be re-entered.
- **`ZABBIX_*`** are only used to seed a single default server on the very first
  run when no server exists yet. Leave them blank to configure everything in-app.

Environment variables for running as a service (the Docker image sets them):

| Variable | Effect |
|----------|--------|
| `ZRS_HOST` | Address to listen on. When set, the app is served by Waitress and no browser is opened. |
| `ZRS_PORT` | Port (default `5050`). |
| `ZRS_DATA_DIR` | Folder for `users.db`, `cache.db` and `secret.key`. |
| `ZRS_NO_SCHEDULER=1` | Do not start the scheduled-report thread. |

---

## 🔐 Security

- Zabbix server passwords are encrypted at rest with an HMAC-verified,
  SHA-256-based stream cipher derived from `SECRET_KEY` (see `security.py`).
- The SMTP password is stored the same way.
- User passwords are stored as salted hashes (Werkzeug) and never shown; plaintext
  passwords from an older database are hashed at the first start after upgrading.
- The default `admin` / `Admin123!` password **must be changed at first sign-in** —
  the app refuses everything else until it is. This also applies after upgrading
  an installation that still uses it.
- **HTTPS certificates of Zabbix servers are verified.** For a server with a
  self-signed certificate, untick *Verify TLS certificate* in its settings; that
  connection can then be intercepted, so prefer a proper certificate.
  After upgrading, existing HTTPS servers with a self-signed certificate report
  "TLS certificate verification failed" until you do this.
- `secret.key`, `.env`, `*.db` and build artifacts are **git-ignored** and must
  never be committed.
- The app binds to `127.0.0.1` only (unless `ZRS_HOST` says otherwise). Do not
  expose it directly to the internet; put it behind a reverse proxy with TLS if
  remote access is needed.
- **Cross-site requests are refused**: a state-changing request must come from the
  app's own pages (checked with the browser's `Sec-Fetch-Site` / `Origin` /
  `Referer` headers; the session cookie is `SameSite=Lax`). A reverse proxy must
  pass the original `Host` header for this to work.
- **Sign-in attempts are limited**: 5 failures per user and address (30 per
  address) within 5 minutes lock further attempts until the window has passed.
- Report jobs can only be read by the user who started them; clearing the cache
  only affects the active server.

---

## 📁 Project structure

```
zabbix-reports/
├── app.py                    ← Flask app: routes, Zabbix API, reports
├── db.py                     ← SQLite event cache
├── reporting.py              ← scheduled reports: KPIs, Excel, e-mail, schedule dates
├── security.py               ← password encryption helpers
├── paths.py                  ← resource/data path handling (dev & frozen)
├── desktop.py                ← desktop-window launcher (pywebview)
├── requirements.txt
├── run.bat / run-app.bat     ← Windows launchers (web / desktop)
├── desktop.spec              ← PyInstaller build config
├── installer.iss             ← Inno Setup installer script
├── Dockerfile                ← container image (web mode)
├── tests/                    ← pytest suite (fake Zabbix + SMTP)
├── static/i18n.js            ← UI language: Turkish dictionary + EN/TR switch
├── static/alarm/             ← JS & CSS
├── templates/                ← Jinja2 HTML templates
├── .env.example              ← example configuration
├── users.db                  ← accounts + servers (auto-created, git-ignored)
└── cache.db                  ← event cache      (auto-created, git-ignored)
```

---

## 🧪 Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

The suite runs the real app against an in-process fake Zabbix API (an old and a
new version) and a fake SMTP server; nothing leaves the machine and no data of
the working copy is touched.

---

## 📦 Building a Windows executable / installer

```bash
pip install pyinstaller
pyinstaller desktop.spec --noconfirm      # -> dist/Zabbix Report Studio/
ISCC.exe installer.iss                     # -> installer/ZabbixReportStudio-Setup.exe (needs Inno Setup)
```

The packaged app stores its writable data (databases, logs, `secret.key`) in
`%LOCALAPPDATA%\Zabbix Report Studio`, so it survives updates and works even when
installed in a read-only location.

---

## 📝 License

Released under the [MIT License](LICENSE) — © 2026 cemilcan0.

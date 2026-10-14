# -*- coding: utf-8 -*-
import io, os, json, time as time_module, uuid, threading, hmac
import requests as req_lib, urllib3
from datetime import datetime, timedelta
from collections import defaultdict
from functools import wraps
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, jsonify, send_file, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
import db as cache_db
import paths
import reporting
from security import encrypt_secret, decrypt_secret

# Load .env if present (also bundled inside the packaged .exe). Optional: the app
# also works without it (SECRET_KEY is persisted separately, servers are added in-app).
try:
    from dotenv import load_dotenv
    _env_path = paths.resource_path('.env')
    if not os.path.exists(_env_path):
        _env_path = paths.data_path('.env')
    load_dotenv(_env_path)
except ImportError:
    pass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Writable data directory (frozen: %LOCALAPPDATA%\Zabbix Report Studio, dev: project folder)
_BASE_DIR = paths.DATA_DIR
# If a seed users.db exists, it is copied to DATA_DIR on first run (existing accounts/servers preserved)
_USERS_DB = paths.seed_file("users.db")
_SQLITE_URI = "sqlite:///" + _USERS_DB.replace("\\", "/")

app = Flask(
    __name__,
    template_folder=paths.resource_path('templates'),
    static_folder=paths.resource_path('static'),
)
def _load_secret_key():
    """Return the app secret key used to encrypt stored Zabbix passwords.

    Priority: the SECRET_KEY environment variable, then a persistent "secret.key"
    file in the writable data directory. If neither exists, a random key is
    generated once and saved so the key stays stable across restarts (and any
    previously encrypted server passwords remain decryptable)."""
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    key_path = paths.data_path('secret.key')
    try:
        if os.path.exists(key_path):
            existing = open(key_path, encoding='utf-8').read().strip()
            if existing:
                return existing
    except Exception:
        pass
    import secrets as _secrets
    generated = _secrets.token_urlsafe(32)
    try:
        with open(key_path, 'w', encoding='utf-8') as f:
            f.write(generated)
    except Exception:
        pass
    return generated


app.config['SECRET_KEY'] = _load_secret_key()
app.config['SQLALCHEMY_DATABASE_URI'] = _SQLITE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

sdb = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ─── DESKTOP MODE: custom title bar (server-side) ────
# desktop.py sets ZRS_DESKTOP=1 BEFORE importing app. In this mode a themed
# custom title bar is added to every HTML page (instead of the native white bar).
# It is added server-side, so pywebview's evaluate_js is not used (no freezes).
_ZRS_DESKTOP = os.environ.get("ZRS_DESKTOP") == "1"

_TITLEBAR_HTML = r"""<style>
#zrs-titlebar{position:fixed;top:0;left:0;right:0;height:36px;z-index:2147483647;display:flex;align-items:center;justify-content:space-between;background:#14110b;border-bottom:1px solid rgba(240,230,200,.1);font-family:'IBM Plex Sans',system-ui,sans-serif;user-select:none;-webkit-user-select:none}
#zrs-titlebar .zrs-tb-drag{display:flex;align-items:center;gap:8px;padding:0 12px;height:100%;flex:1;min-width:0}
#zrs-titlebar .zrs-tb-drag span{font-size:12px;font-weight:600;color:#c7c0b0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#zrs-titlebar .zrs-tb-btns{display:flex;height:100%}
#zrs-titlebar .zrs-tb-btn{width:46px;height:100%;border:none;background:transparent;color:#a59f91;font-size:15px;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .12s,color .12s;font-family:inherit}
#zrs-titlebar .zrs-tb-btn:hover{background:rgba(240,230,200,.08);color:#f3efe6}
#zrs-titlebar .zrs-tb-close:hover{background:#e11d48;color:#fff}
body{padding-top:36px !important}
#zrsSidebar{top:36px !important;height:calc(100vh - 36px) !important}
.app-header{top:36px !important}
.toolbar{top:100px !important}
.layout{min-height:calc(100vh - 36px) !important}
.sidebar{top:36px !important;height:calc(100vh - 36px) !important}
.main{max-height:calc(100vh - 36px) !important}
</style>
<div id="zrs-titlebar">
  <div class="zrs-tb-drag pywebview-drag-region" id="zrs-drag"><img src="/static/favicon.svg" width="16" height="16" style="border-radius:4px" alt=""><span>Zabbix Report Studio</span></div>
  <div class="zrs-tb-btns">
    <button class="zrs-tb-btn" id="zrs-min" title="Minimize">&#8211;</button>
    <button class="zrs-tb-btn" id="zrs-max" title="Maximize / Restore">&#9633;</button>
    <button class="zrs-tb-btn zrs-tb-close" id="zrs-close" title="Close">&#10005;</button>
  </div>
</div>
<script>
(function(){
  function api(){return (window.pywebview&&window.pywebview.api)?window.pywebview.api:null;}
  var mn=document.getElementById('zrs-min'),mx=document.getElementById('zrs-max'),cl=document.getElementById('zrs-close'),dr=document.getElementById('zrs-drag');
  if(mn)mn.addEventListener('click',function(){var a=api();if(a)a.minimize();});
  if(mx)mx.addEventListener('click',function(){var a=api();if(a)a.toggle_maximize();});
  if(cl)cl.addEventListener('click',function(){var a=api();if(a)a.close();});
  if(dr)dr.addEventListener('dblclick',function(){var a=api();if(a)a.toggle_maximize();});
})();
</script>"""


@app.after_request
def _inject_desktop_titlebar(resp):
    if not _ZRS_DESKTOP:
        return resp
    try:
        if resp.direct_passthrough:
            return resp
        if "text/html" not in (resp.headers.get("Content-Type") or ""):
            return resp
        html = resp.get_data(as_text=True)
        gt = html.lower().find("<body")
        if gt == -1:
            return resp
        gt = html.find(">", gt)
        if gt == -1:
            return resp
        resp.set_data(html[:gt + 1] + _TITLEBAR_HTML + html[gt + 1:])
    except Exception:
        pass
    return resp

# Alarm Zabbix sessions and report jobs
alarm_sessions = {}
report_jobs = {}
ack_jobs = {}

# ═══════════════════════════════════════════════
# On-call (nobet) roster — shows who was on duty at the moment of an alarm in the
# ACK report. Settings live in the DB (nobet_config, a single shared row) and are
# managed by an admin from the /nobet screen. Model: a repeating N-day cycle with
# configurable shift hours.
# ═══════════════════════════════════════════════
DEFAULT_DAY_START   = 8    # day shift start hour (default 08:00)
DEFAULT_NIGHT_START = 20   # night shift start hour (default 20:00)


def load_nobet_config():
    """Read the nobet_config row and return a dict ready for nobetci_for().
    Returns None if not configured/incomplete (the on-call column then stays empty)."""
    cfg = NobetConfig.query.get(1)
    if not cfg or not cfg.cycle_start:
        return None
    try:
        baslangic = datetime.strptime(cfg.cycle_start, '%Y-%m-%d').date()
    except ValueError:
        return None
    try:
        dongu = json.loads(cfg.cycle_json or '[]')
    except (ValueError, TypeError):
        dongu = []
    dongu = [{'gunduz': (d or {}).get('gunduz', ''), 'gece': (d or {}).get('gece', '')}
             for d in dongu if isinstance(d, dict)]
    if not dongu:
        return None
    return {
        'baslangic':   baslangic,
        'dongu':       dongu,
        'dongu_len':   len(dongu),
        'day_start':   cfg.day_start_hour if cfg.day_start_hour is not None else DEFAULT_DAY_START,
        'night_start': cfg.night_start_hour if cfg.night_start_hour is not None else DEFAULT_NIGHT_START,
    }


def nobetci_for(ts, cfg):
    """Return the person on duty at the given unix timestamp.
    cfg = output of load_nobet_config(); returns '' if None.
    Day shift covers [day_start, night_start); night shift covers the rest.
    The part of the night shift after midnight (hour < day_start) belongs to the
    PREVIOUS calendar day — this keeps the shift-boundary offset correct."""
    if not cfg:
        return ''
    dt = datetime.fromtimestamp(ts)
    hour = dt.hour
    ds, ns = cfg['day_start'], cfg['night_start']

    if ds <= hour < ns:
        vardiya = 'gunduz'
        vardiya_gun = dt.date()
    else:
        vardiya = 'gece'
        # After midnight (00:00 - day_start) -> previous day's night shift
        if hour < ds:
            vardiya_gun = (dt - timedelta(days=1)).date()
        else:
            vardiya_gun = dt.date()

    delta_days = (vardiya_gun - cfg['baslangic']).days
    idx = delta_days % cfg['dongu_len']   # 0-indexed cycle list
    entry = cfg['dongu'][idx]
    return entry.get(vardiya, '')


def _seed_nobet_from_txt():
    """If a legacy nobet.txt exists, migrate its contents into the DB once
    (upgrade convenience; no-ops when the file is absent)."""
    path = os.path.join(_BASE_DIR, 'nobet.txt')
    if not os.path.exists(path):
        return
    baslangic = ''
    days = {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                key, val = key.strip(), val.strip()
                if key.upper() == 'DONGU_BASLANGIC':
                    baslangic = val
                elif key.isdigit() and '|' in val:
                    parts = [p.strip() for p in val.split('|')]
                    days[int(key)] = {'gunduz': parts[0], 'gece': parts[1] if len(parts) > 1 else ''}
        if not baslangic or not days:
            return
        cycle = [days[k] for k in sorted(days)]
        sdb.session.add(NobetConfig(
            id=1, cycle_start=baslangic,
            day_start_hour=DEFAULT_DAY_START, night_start_hour=DEFAULT_NIGHT_START,
            cycle_json=json.dumps(cycle, ensure_ascii=False)))
        sdb.session.commit()
        print(f">> nobet.txt migrated to DB: {len(cycle)}-day cycle")
    except Exception as e:
        print(f">> nobet.txt migration error: {e}")

# Dashboard (overview) statistics — per-server cache
# server_id -> {"ts": epoch, "data": {...}}
dashboard_stats_cache = {}
DASHBOARD_TTL = 300  # seconds (5 min): repeated queries within this window are served from cache

# ─── SETTINGS ────────────────────────────────────────────
ZABBIX_URL      = os.environ.get("ZABBIX_URL", "http://127.0.0.1/api_jsonrpc.php")
ZABBIX_USER     = os.environ.get("ZABBIX_USER", "admin")
ZABBIX_PASSWORD = os.environ.get("ZABBIX_PASSWORD", "")

# Item names to look up when resolving a host's serial number.
SERIAL_ITEM_NAMES = ["Hardware serial number","Chassis: Hardware serial number","Appliance serial number"]
# Single category name: all hosts on the server.
ALL_CAT = "All Devices"


# ─── USER MODEL ────────────────────────────────────────
# Roles: admin (everything), user (own servers and schedules), viewer (read-only:
# uses the servers defined by admins, cannot change anything).
ROLES = ('admin', 'user', 'viewer')
DEFAULT_ADMIN_PASSWORD = 'Admin123!'
MIN_PASSWORD_LEN = 8
_HASH_PREFIXES = ('scrypt:', 'pbkdf2:')


class User(UserMixin, sdb.Model):
    __tablename__ = 'users'
    id         = sdb.Column(sdb.Integer, primary_key=True)
    username   = sdb.Column(sdb.String(80), unique=True, nullable=False)
    password   = sdb.Column(sdb.String(200), nullable=False)   # werkzeug password hash
    role       = sdb.Column(sdb.String(20), default='user')
    created_at = sdb.Column(sdb.DateTime, default=datetime.utcnow)
    # Set for the default admin and for passwords typed by an admin: the user
    # must pick a new password before using the app.
    must_change_password = sdb.Column(sdb.Boolean, nullable=False, default=False)

    def is_admin(self): return self.role == 'admin'
    def can_write(self): return self.role != 'viewer'

    def password_is_hashed(self):
        return (self.password or '').startswith(_HASH_PREFIXES)

    def set_password(self, raw):
        self.password = generate_password_hash(raw)

    def check_password(self, raw):
        if self.password_is_hashed():
            return check_password_hash(self.password, raw)
        # Row from before passwords were hashed (upgraded by _migrate_schema)
        return hmac.compare_digest((self.password or '').encode(), (raw or '').encode())

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ─── ZABBIX SERVER MODEL (per user) ────────────
class ZabbixServer(sdb.Model):
    __tablename__ = 'zabbix_servers'
    id           = sdb.Column(sdb.Integer, primary_key=True)
    user_id      = sdb.Column(sdb.Integer, sdb.ForeignKey('users.id'), nullable=False, index=True)
    name         = sdb.Column(sdb.String(120), nullable=False)
    base_url     = sdb.Column(sdb.String(300), nullable=False)   # without api_jsonrpc.php
    username     = sdb.Column(sdb.String(120), nullable=False)
    password_enc = sdb.Column(sdb.Text, nullable=False)          # stored encrypted
    verify_tls   = sdb.Column(sdb.Boolean, nullable=False, default=True)  # check the HTTPS certificate
    created_at   = sdb.Column(sdb.DateTime, default=datetime.utcnow)

    @property
    def api_url(self):
        return self.base_url.rstrip('/') + '/api_jsonrpc.php'

    def get_password(self):
        try:
            return decrypt_secret(self.password_enc, app.config['SECRET_KEY'])
        except Exception:
            return ''


# ─── BUSINESS HOURS (shared, single row id=1; admin managed) ────
# Splits alarms into "Mesai İçi" (in hours) / "Mesai Dışı" (after hours). The
# split is computed from the alarm time whenever events are returned, so changing
# the hours also applies to events that are already in the cache.
class BusinessHours(sdb.Model):
    __tablename__ = 'business_hours'
    id         = sdb.Column(sdb.Integer, primary_key=True)
    start_hour = sdb.Column(sdb.Integer, nullable=False, default=8)    # inclusive
    end_hour   = sdb.Column(sdb.Integer, nullable=False, default=18)   # exclusive
    days       = sdb.Column(sdb.String(20), nullable=False, default='0,1,2,3,4')   # 0 = Monday


DEFAULT_BUSINESS_HOURS = {'start': 8, 'end': 18, 'days': [0, 1, 2, 3, 4]}
# Read by report threads, which have no request context: kept in memory,
# refreshed at startup and whenever an admin saves the setting.
_business_hours = dict(DEFAULT_BUSINESS_HOURS)


def load_business_hours():
    row = BusinessHours.query.get(1)
    if row:
        days = sorted({int(d) for d in (row.days or '').split(',') if d.strip().isdigit() and 0 <= int(d) <= 6})
        _business_hours.update(start=row.start_hour, end=row.end_hour, days=days)
    return dict(_business_hours)


def mesai_for(clock):
    """'Mesai İçi' if the unix time falls inside business hours, else 'Mesai Dışı'."""
    dt = datetime.fromtimestamp(int(clock))
    inside = dt.weekday() in _business_hours['days'] and _business_hours['start'] <= dt.hour < _business_hours['end']
    return 'Mesai İçi' if inside else 'Mesai Dışı'


def with_business_hours(events):
    """Set the shift of each event from the current business hours."""
    for e in events:
        e['mesai'] = mesai_for(e.get('clock') or 0)
    return events


# ─── ON-CALL (NOBET) MODEL (shared, single row id=1; admin managed) ────
class NobetConfig(sdb.Model):
    __tablename__ = 'nobet_config'
    id               = sdb.Column(sdb.Integer, primary_key=True)
    cycle_start      = sdb.Column(sdb.String(10), default='')   # YYYY-MM-DD (day 1 of the cycle)
    day_start_hour   = sdb.Column(sdb.Integer, default=8)       # day shift start hour
    night_start_hour = sdb.Column(sdb.Integer, default=20)      # night shift start hour
    cycle_json       = sdb.Column(sdb.Text, default='[]')       # [{"gunduz":"","gece":""}, ...]
    updated_at       = sdb.Column(sdb.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def normalize_base_url(url):
    """Normalize a user-entered URL to its canonical 'base' form
    (strips trailing / and /api_jsonrpc.php)."""
    url = (url or '').strip().rstrip('/')
    if url.lower().endswith('/api_jsonrpc.php'):
        url = url[:-len('/api_jsonrpc.php')]
    return url.rstrip('/')


def visible_servers():
    """Query of the servers the signed-in user may use: their own, or — for a
    read-only viewer, who cannot add servers — the ones defined by admins."""
    if current_user.role == 'viewer':
        admin_ids = [u.id for u in User.query.filter_by(role='admin').all()]
        return ZabbixServer.query.filter(ZabbixServer.user_id.in_(admin_ids))
    return ZabbixServer.query.filter_by(user_id=current_user.id)


def server_config(s):
    """Connection info dict for a ZabbixServer row."""
    return {
        'id': s.id, 'name': s.name,
        'base_url': s.base_url, 'api_url': s.api_url,
        'user': s.username, 'password': s.get_password(),
        'verify_tls': s.verify_tls is not False,
    }


def get_active_server_config():
    """Return the connection info of the active Zabbix server for the session.
    Returns None if no server is selected."""
    sid = session.get('active_zabbix_id')
    if not sid:
        return None
    s = visible_servers().filter(ZabbixServer.id == sid).first()
    return server_config(s) if s else None


def write_required(view):
    """Reject the request for read-only (viewer) accounts."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.can_write():
            return jsonify({"success": False, "error": "This account is read-only."}), 403
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_admin():
            return jsonify({"success": False, "error": "Admin privileges are required for this action."}), 403
        return view(*args, **kwargs)
    return wrapper


def _own_job(jobs, job_id):
    """The background job with this id, if the signed-in user started it."""
    job = jobs.get(job_id) if job_id else None
    return job if job and job.get('user') == current_user.id else None


def _alarm_session(data):
    """The alarm session named in the request, if it belongs to the signed-in user."""
    found = alarm_sessions.get((data or {}).get('sessionId'))
    return found if found and found.get('user') == current_user.id else None


def _no_active_server_response():
    return jsonify({
        "success": False, "error": "No active Zabbix server selected.",
        "needServer": True
    }), 400


def _migrate_schema():
    """Bring a database created by an older version up to date: add the columns
    create_all() does not add to existing tables, and hash plaintext passwords."""
    def add_column(table, column, ddl):
        cols = [r[1] for r in sdb.session.execute(sdb.text(f"PRAGMA table_info({table})")).fetchall()]
        if column not in cols:
            sdb.session.execute(sdb.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            sdb.session.commit()
    add_column('users', 'must_change_password', 'BOOLEAN NOT NULL DEFAULT 0')
    add_column('zabbix_servers', 'verify_tls', 'BOOLEAN NOT NULL DEFAULT 1')
    changed = False
    for u in User.query.all():
        if not u.password_is_hashed():
            # The well-known default must not survive the upgrade
            if u.password == DEFAULT_ADMIN_PASSWORD:
                u.must_change_password = True
            u.set_password(u.password)
            changed = True
    if changed:
        sdb.session.commit()
        print(">> Stored user passwords were hashed")


def create_admin():
    with app.app_context():
        sdb.create_all()
        _migrate_schema()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', role='admin', must_change_password=True)
            admin.set_password(DEFAULT_ADMIN_PASSWORD)
            sdb.session.add(admin)
            sdb.session.commit()
            print(f">> Admin created: admin / {DEFAULT_ADMIN_PASSWORD} (must be changed at first sign-in)")
        # Seed the .env Zabbix as a server for admin once, if configured (easy start)
        admin = User.query.filter_by(username='admin').first()
        if admin and ZABBIX_URL and not ZabbixServer.query.filter_by(user_id=admin.id).first():
            sdb.session.add(ZabbixServer(
                user_id=admin.id, name="Default Zabbix",
                base_url=normalize_base_url(ZABBIX_URL),
                username=ZABBIX_USER,
                password_enc=encrypt_secret(ZABBIX_PASSWORD, app.config['SECRET_KEY']),
            ))
            sdb.session.commit()
            print(">> .env Zabbix server added to the admin account")
        # If nobet.txt exists and no on-call config is in the DB yet, import it once
        if not NobetConfig.query.get(1):
            _seed_nobet_from_txt()
        load_business_hours()
    cache_db.init_db()
    start_scheduler()


# ─── AUTH ROUTES ────────────────────────────────────────
# Failed sign-ins per (client address, username) and per client address, kept in
# memory: after too many within the window further attempts are refused.
LOGIN_WINDOW = 300          # seconds
LOGIN_MAX_PER_USER = 5
LOGIN_MAX_PER_ADDRESS = 30
_failed_logins = defaultdict(list)   # key -> [timestamps]


def _login_keys(username):
    address = request.remote_addr or '?'
    return (address, username.lower()), (address, None)


def _login_blocked(username):
    cutoff = time_module.time() - LOGIN_WINDOW
    for key, limit in zip(_login_keys(username), (LOGIN_MAX_PER_USER, LOGIN_MAX_PER_ADDRESS)):
        recent = [t for t in _failed_logins.get(key, ()) if t > cutoff]
        if recent:
            _failed_logins[key] = recent
        else:
            _failed_logins.pop(key, None)
        if len(recent) >= limit:
            return True
    return False


def _login_failed(username):
    for key in _login_keys(username):
        _failed_logins[key].append(time_module.time())


@app.route('/login', methods=['GET','POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        if _login_blocked(username):
            flash('Too many failed attempts. Try again in a few minutes.', 'error')
            return render_template('login.html'), 429
        u = User.query.filter_by(username=username).first()
        if u and u.check_password(request.form.get('password','')):
            _failed_logins.pop(_login_keys(username)[0], None)
            login_user(u)
            if u.must_change_password:
                return redirect(url_for('change_password'))
            # Auto-activate the server for a user who has exactly one
            servers = visible_servers().all()
            if len(servers) == 1:
                session['active_zabbix_id'] = servers[0].id
            return redirect(url_for('servers_page'))
        _login_failed(username)
        flash('Invalid username or password!', 'error')
    return render_template('login.html')


def password_problem(new, current=None):
    """Why `new` is not acceptable as a password, or None if it is."""
    if len(new) < MIN_PASSWORD_LEN:
        return f'The password must be at least {MIN_PASSWORD_LEN} characters long.'
    if new == DEFAULT_ADMIN_PASSWORD:
        return 'The default password cannot be used.'
    if current is not None and new == current:
        return 'The new password must be different from the current one.'
    return None


@app.route('/change-password', methods=['GET','POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current = request.form.get('current_password','')
        new = request.form.get('new_password','')
        if not current_user.check_password(current):
            flash('The current password is wrong.', 'error')
        elif new != request.form.get('confirm_password',''):
            flash('The new passwords do not match.', 'error')
        elif password_problem(new, current):
            flash(password_problem(new, current), 'error')
        else:
            current_user.set_password(new)
            current_user.must_change_password = False
            sdb.session.commit()
            servers = visible_servers().all()
            if len(servers) == 1 and not session.get('active_zabbix_id'):
                session['active_zabbix_id'] = servers[0].id
            return redirect(url_for('servers_page'))
    return render_template('change_password.html', forced=current_user.must_change_password,
                           min_len=MIN_PASSWORD_LEN)


@app.before_request
def _reject_cross_site_requests():
    """CSRF protection without tokens: a browser says where a POST comes from
    (Sec-Fetch-Site, Origin, Referer); anything not from this app's own pages is
    refused. Requests without those headers are not from a browser page."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    site = request.headers.get('Sec-Fetch-Site')
    source = request.headers.get('Origin') or request.headers.get('Referer')
    if (site and site not in ('same-origin', 'none')) or \
            (source and urlsplit(source).netloc != request.host):
        return jsonify({"success": False, "error": "Cross-site request refused."}), 403
    return None


@app.before_request
def _require_password_change():
    """Until a flagged user has picked a new password, nothing else is reachable."""
    if not current_user.is_authenticated or not current_user.must_change_password:
        return None
    if request.endpoint in ('change_password', 'logout', 'static'):
        return None
    if '/api/' in request.path:
        return jsonify({"success": False, "mustChangePassword": True,
                        "error": "You must change your password first."}), 403
    return redirect(url_for('change_password'))

@app.route('/logout')
@login_required
def logout():
    session.pop('active_zabbix_id', None)
    logout_user()
    return redirect(url_for('login'))


# ─── ZABBIX SERVER MANAGEMENT ─────────────────────────────
@app.route('/servers')
@login_required
def servers_page():
    return render_template('servers.html')


@app.route('/api/servers', methods=['GET'])
@login_required
def api_list_servers():
    servers = visible_servers().order_by(ZabbixServer.created_at.asc()).all()
    active_id = session.get('active_zabbix_id')
    return jsonify({
        "success": True,
        "activeId": active_id,
        "canManage": current_user.can_write(),
        "servers": [{
            "id": s.id, "name": s.name, "baseUrl": s.base_url,
            "username": s.username, "isActive": (s.id == active_id),
            "verifyTls": s.verify_tls is not False
        } for s in servers]
    })


@app.route('/api/servers', methods=['POST'])
@login_required
@write_required
def api_create_server():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    url = normalize_base_url(data.get('url'))
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not name or not url or not username:
        return jsonify({"success": False, "error": "Name, URL and username are required."}), 400
    s = ZabbixServer(
        user_id=current_user.id, name=name, base_url=url, username=username,
        password_enc=encrypt_secret(password, app.config['SECRET_KEY']),
        verify_tls=data.get('verifyTls', True) is not False
    )
    sdb.session.add(s)
    sdb.session.commit()
    return jsonify({"success": True, "id": s.id})


@app.route('/api/servers/<int:sid>', methods=['POST'])
@login_required
@write_required
def api_update_server(sid):
    s = ZabbixServer.query.filter_by(id=sid, user_id=current_user.id).first()
    if not s:
        return jsonify({"success": False, "error": "Server not found."}), 404
    data = request.json or {}
    name = (data.get('name') or '').strip()
    url = normalize_base_url(data.get('url'))
    username = (data.get('username') or '').strip()
    if not name or not url or not username:
        return jsonify({"success": False, "error": "Name, URL and username are required."}), 400
    s.name = name
    s.base_url = url
    s.username = username
    if 'verifyTls' in data:
        s.verify_tls = data.get('verifyTls') is not False
    # If the password is left blank, the existing password is kept
    if data.get('password'):
        s.password_enc = encrypt_secret(data.get('password'), app.config['SECRET_KEY'])
    sdb.session.commit()
    return jsonify({"success": True})


@app.route('/api/servers/<int:sid>/delete', methods=['POST'])
@login_required
@write_required
def api_delete_server(sid):
    s = ZabbixServer.query.filter_by(id=sid, user_id=current_user.id).first()
    if not s:
        return jsonify({"success": False, "error": "Server not found."}), 404
    ReportSchedule.query.filter_by(server_id=s.id).delete()
    sdb.session.delete(s)
    sdb.session.commit()
    if session.get('active_zabbix_id') == sid:
        session.pop('active_zabbix_id', None)
    return jsonify({"success": True})


@app.route('/api/servers/<int:sid>/activate', methods=['POST'])
@login_required
def api_activate_server(sid):
    s = visible_servers().filter(ZabbixServer.id == sid).first()
    if not s:
        return jsonify({"success": False, "error": "Server not found."}), 404
    session['active_zabbix_id'] = sid
    return jsonify({"success": True})


def _test_server_login(api_url, username, password, verify=True):
    """Try to log in to a single Zabbix server — reachable/authorized?"""
    try:
        try:
            token = alarm_zabbix_req_url(api_url, "user.login",
                                        {"username": username, "password": password}, timeout=6, verify=verify)
        except Exception as e:
            if str(e) == TLS_ERROR:
                raise
            token = alarm_zabbix_req_url(api_url, "user.login",
                                        {"user": username, "password": password}, timeout=6, verify=verify)
        # Log out (if we got a token)
        if token:
            try:
                alarm_zabbix_req_url(api_url, "user.logout", {}, auth=token, timeout=6, verify=verify)
            except Exception:
                pass
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


@app.route('/api/servers/check', methods=['POST'])
@login_required
def api_check_servers():
    """Parallel connectivity test to all of the user's servers — for green/red status."""
    servers = visible_servers().all()
    jobs = [(s.id, s.api_url, s.username, s.get_password(), s.verify_tls is not False) for s in servers]
    results = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
            futs = {ex.submit(_test_server_login, u, un, pw, vf): sid for sid, u, un, pw, vf in jobs}
            for fut in futs:
                sid = futs[fut]
                try:
                    results[sid] = fut.result()
                except Exception as e:
                    results[sid] = {"ok": False, "error": str(e)[:160]}
    return jsonify({"success": True, "results": {str(k): v for k, v in results.items()}})


# ─── DASHBOARD ──────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    return render_template('dashboard.html', now=datetime.now(),
                           active_server=get_active_server_config())


def compute_dashboard_stats(cfg):
    """Compute the Overview (dashboard) KPIs live for the active Zabbix server:
    active problems, 30-day total alarms, severity distribution, top alarming
    hosts, monitored host count and average/median resolution time."""
    zapi = zapi_for(cfg)
    zapi.login()
    try:
        now = int(time_module.time())
        since = now - 30 * 24 * 3600

        # ── Active (unresolved) problems + severity breakdown ──
        # The raw problem.get count did not match the real active-alarm count on
        # the Alarm page: disabled triggers/items, triggers with no active host,
        # and problems suppressed by maintenance or dependency were being counted.
        # We apply the same filter as the Alarm page's "active" mode to get the
        # real number.
        raw_problems = zapi._post("problem.get", {
            "output": ["eventid", "severity", "clock", "objectid"],
            "sortfield": ["eventid"], "sortorder": "DESC",
            "limit": 20000
        }) or []

        trig_ids = list({p.get("objectid") for p in raw_problems if p.get("objectid")})
        disabled_triggers = set()
        maintenance_triggers = set()
        trig_info = {}
        for i in range(0, len(trig_ids), 500):
            batch = trig_ids[i:i + 500]
            trigs = zapi._post("trigger.get", {
                "triggerids": batch,
                "selectHosts": ["hostid", "status", "maintenance_status"],
                "selectItems": ["itemid", "status"],
                "selectDependencies": ["triggerid", "status"],
                "output": ["triggerid", "status"]
            }) or []
            for t in trigs:
                tid = t["triggerid"]
                trig_info[tid] = t
                if t.get("status") == "1":
                    disabled_triggers.add(tid); continue
                items = t.get("items", [])
                if items and not [it for it in items if it.get("status") == "0"]:
                    disabled_triggers.add(tid); continue
                active_hosts = [h for h in t.get("hosts", []) if h.get("status") == "0"]
                if not active_hosts:
                    disabled_triggers.add(tid); continue
                if len([h for h in active_hosts if h.get("maintenance_status") == "1"]) == len(active_hosts):
                    maintenance_triggers.add(tid)

        # Triggers suppressed by a dependency
        active_trigs = {p["objectid"] for p in raw_problems
                        if p.get("objectid") and p["objectid"] not in disabled_triggers}
        suppressed_triggers = set()
        for tid in active_trigs:
            t = trig_info.get(tid)
            if not t:
                continue
            for dep in t.get("dependencies", []):
                if dep.get("triggerid") in active_trigs and dep.get("status") != "1":
                    suppressed_triggers.add(tid); break

        problems = [p for p in raw_problems
                    if p.get("objectid") not in disabled_triggers
                    and p.get("objectid") not in maintenance_triggers
                    and p.get("objectid") not in suppressed_triggers]
        active_count = len(problems)
        active_sev = defaultdict(int)
        for p in problems:
            active_sev[str(p.get("severity", "0"))] += 1
        app.logger.info(
            f"[dashboard] active raw={len(raw_problems)} disabled={len(disabled_triggers)} "
            f"maintenance={len(maintenance_triggers)} suppressed={len(suppressed_triggers)} "
            f"active={active_count}"
        )

        # ── Monitored (active) host count ──
        try:
            host_cnt = zapi._post("host.get", {"countOutput": True, "filter": {"status": "0"}})
            monitored_hosts = int(host_cnt) if host_cnt is not None else 0
        except Exception:
            monitored_hosts = 0

        # ── Problem events of the last 30 days (paginated) ──
        events = []
        params = {
            "output": ["eventid", "severity", "clock", "r_eventid", "name", "objectid"],
            "selectHosts": ["hostid", "name"],
            "source": 0, "object": 0, "value": 1,
            "time_from": since,
            "sortfield": ["eventid"], "sortorder": "ASC", "limit": 10000
        }
        page = zapi._post("event.get", params) or []
        events.extend(page)
        guard = 0
        while len(page) >= 10000 and guard < 30:
            guard += 1
            last_clock = page[-1].get("clock", str(since))
            np = dict(params); np["time_from"] = last_clock
            page = zapi._post("event.get", np) or []
            seen = {e["eventid"] for e in events}
            newev = [e for e in page if e["eventid"] not in seen]
            events.extend(newev)
            if not newev:
                break

        # Bulk-fetch the clock of recovery events for resolution time
        # (event.get output has no r_clock, only r_eventid)
        r_eventids = list({e.get("r_eventid") for e in events
                           if e.get("r_eventid") and e.get("r_eventid") != "0"})
        r_clock_map = {}
        for i in range(0, len(r_eventids), 1000):
            batch = r_eventids[i:i + 1000]
            revs = zapi._post("event.get", {"output": ["eventid", "clock"], "eventids": batch}) or []
            for rv in revs:
                r_clock_map[rv["eventid"]] = int(rv.get("clock") or 0)

        total_30 = len(events)
        sev_dist = defaultdict(int)
        host_counts = defaultdict(int)
        durations = []
        for e in events:
            sev_dist[str(e.get("severity", "0"))] += 1
            for h in (e.get("hosts") or []):
                nm = (h.get("name") or h.get("host") or "?").strip() or "?"
                host_counts[nm] += 1
            c = int(e.get("clock") or 0)
            rc = r_clock_map.get(e.get("r_eventid"), 0)
            if c > 0 and rc >= c and rc > 0:
                durations.append(rc - c)

        top_hosts = sorted(host_counts.items(), key=lambda x: x[1], reverse=True)[:6]

        avg_sec = int(sum(durations) / len(durations)) if durations else 0
        med_sec = 0
        if durations:
            sd = sorted(durations); n = len(sd)
            med_sec = sd[n // 2] if n % 2 else (sd[n // 2 - 1] + sd[n // 2]) // 2

        return {
            "activeProblems": active_count,
            "activeBySeverity": dict(active_sev),
            "total30": total_30,
            "severityDist": dict(sev_dist),
            "topHosts": [{"name": n, "count": c} for n, c in top_hosts],
            "monitoredHosts": monitored_hosts,
            "avgResolveSec": avg_sec,
            "medianResolveSec": med_sec,
            "resolvedSampleCount": len(durations),
        }
    finally:
        try:
            zapi.logout()
        except Exception:
            pass


@app.route('/api/dashboard/stats', methods=['GET'])
@login_required
def api_dashboard_stats():
    """Overview KPIs — cached per server. Within a window (DASHBOARD_TTL), requests
    for the same server are served from cache; ?force=1 bypasses the cache. A
    different server produces a different key => fresh data automatically."""
    cfg = get_active_server_config()
    if not cfg:
        return _no_active_server_response()
    sid = cfg['id']
    force = request.args.get('force') == '1'
    now = time_module.time()
    entry = dashboard_stats_cache.get(sid)

    if entry and not force and (now - entry['ts'] < DASHBOARD_TTL):
        return jsonify({"success": True, "data": entry['data'], "server": cfg['name'],
                        "cachedAt": entry['ts'], "age": int(now - entry['ts']),
                        "ttl": DASHBOARD_TTL, "fromCache": True})
    try:
        data = compute_dashboard_stats(cfg)
    except Exception as e:
        # On error, return stale data if we still have some (flagged as stale)
        if entry:
            return jsonify({"success": True, "data": entry['data'], "server": cfg['name'],
                            "cachedAt": entry['ts'], "age": int(now - entry['ts']),
                            "ttl": DASHBOARD_TTL, "fromCache": True, "stale": True,
                            "warning": str(e)[:200]})
        return jsonify({"success": False, "error": str(e)[:200]}), 500

    dashboard_stats_cache[sid] = {"ts": now, "data": data}
    return jsonify({"success": True, "data": data, "server": cfg['name'],
                    "cachedAt": now, "age": 0, "ttl": DASHBOARD_TTL, "fromCache": False})

@app.route("/ack")
@login_required
def ack_page():
    return render_template("ack.html")


# ─── ON-CALL (NOBET) MANAGEMENT (shared; admin edits, everyone views) ───
@app.route("/nobet")
@login_required
def nobet_page():
    return render_template("nobet.html")


@app.route("/api/nobet", methods=["GET"])
@login_required
def api_get_nobet():
    cfg = NobetConfig.query.get(1)
    if not cfg:
        return jsonify({"success": True, "canEdit": current_user.is_admin(), "config": {
            "cycleStart": "", "dayStartHour": DEFAULT_DAY_START,
            "nightStartHour": DEFAULT_NIGHT_START, "cycle": []}})
    try:
        cycle = json.loads(cfg.cycle_json or "[]")
    except (ValueError, TypeError):
        cycle = []
    return jsonify({"success": True, "canEdit": current_user.is_admin(), "config": {
        "cycleStart": cfg.cycle_start or "",
        "dayStartHour": cfg.day_start_hour if cfg.day_start_hour is not None else DEFAULT_DAY_START,
        "nightStartHour": cfg.night_start_hour if cfg.night_start_hour is not None else DEFAULT_NIGHT_START,
        "cycle": cycle}})


@app.route("/api/nobet", methods=["POST"])
@login_required
def api_save_nobet():
    if not current_user.is_admin():
        return jsonify({"success": False, "error": "Admin privileges are required for this action."}), 403
    data = request.json or {}

    cycle_start = (data.get("cycleStart") or "").strip()
    if cycle_start:
        try:
            datetime.strptime(cycle_start, "%Y-%m-%d")
        except ValueError:
            return jsonify({"success": False, "error": "Start date must be in YYYY-MM-DD format."}), 400

    try:
        day_h   = int(data.get("dayStartHour", DEFAULT_DAY_START))
        night_h = int(data.get("nightStartHour", DEFAULT_NIGHT_START))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Shift hours must be numbers."}), 400
    if not (0 <= day_h <= 23) or not (0 <= night_h <= 23):
        return jsonify({"success": False, "error": "Shift hours must be between 0 and 23."}), 400
    if night_h <= day_h:
        return jsonify({"success": False, "error": "Night start hour must be after day start."}), 400

    raw_cycle = data.get("cycle", [])
    cycle = []
    for d in (raw_cycle if isinstance(raw_cycle, list) else []):
        if isinstance(d, dict):
            cycle.append({"gunduz": (d.get("gunduz") or "").strip(),
                          "gece":   (d.get("gece") or "").strip()})

    cfg = NobetConfig.query.get(1)
    if not cfg:
        cfg = NobetConfig(id=1)
        sdb.session.add(cfg)
    cfg.cycle_start      = cycle_start
    cfg.day_start_hour   = day_h
    cfg.night_start_hour = night_h
    cfg.cycle_json       = json.dumps(cycle, ensure_ascii=False)
    sdb.session.commit()
    return jsonify({"success": True})


@app.route("/api/business-hours", methods=["GET"])
@login_required
def api_get_business_hours():
    return jsonify({"success": True, "canEdit": current_user.is_admin(), "hours": load_business_hours()})


@app.route("/api/business-hours", methods=["POST"])
@login_required
@admin_required
def api_save_business_hours():
    data = request.json or {}
    try:
        start, end = int(data.get("start")), int(data.get("end"))
        days = sorted({int(d) for d in data.get("days") or []})
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Business hours are not valid."}), 400
    if not (0 <= start < end <= 24) or not days or any(d < 0 or d > 6 for d in days):
        return jsonify({"success": False, "error": "Business hours are not valid."}), 400
    row = BusinessHours.query.get(1)
    if not row:
        row = BusinessHours(id=1)
        sdb.session.add(row)
    row.start_hour, row.end_hour, row.days = start, end, ','.join(str(d) for d in days)
    sdb.session.commit()
    load_business_hours()
    return jsonify({"success": True})


@app.route("/api/ack", methods=["POST"])
@login_required
def start_ack_job():
    """Start the ACK report job — fetched day by day in a background thread."""
    data = request.json
    mode = data.get("mode", "active")
    group_ids = data.get("groupIds", [])
    time_from = data.get("timeFrom")
    time_till = data.get("timeTill")

    cfg = get_active_server_config()
    if not cfg:
        return _no_active_server_response()

    # Load the on-call config in the request context, then pass it to the thread
    nobet_cfg = load_nobet_config()

    job_id = str(uuid.uuid4())
    ack_jobs[job_id] = {
        'status': 'starting',
        'progress': {'percent': 0, 'step': 'Starting...', 'events': 0},
        'result': None, 'error': None, 'cancelled': False,
        'created_at': time_module.time(), 'user': current_user.id
    }
    thread = threading.Thread(
        target=run_ack_job,
        args=(job_id, mode, group_ids, time_from, time_till, cfg, nobet_cfg),
        daemon=True
    )
    thread.start()

    # Clean up old jobs
    now = time_module.time()
    expired = [jid for jid, j in ack_jobs.items() if now - j.get('created_at', 0) > 3600]
    for jid in expired:
        del ack_jobs[jid]

    return jsonify({"success": True, "jobId": job_id})


@app.route("/api/ack/progress", methods=["POST"])
@login_required
def ack_job_progress():
    data = request.json
    job_id = data.get("jobId", "")
    job = _own_job(ack_jobs, job_id)
    if job:
        return jsonify({"success": True, "data": job['progress'], "status": job['status'], "error": job.get('error')})
    return jsonify({"success": True, "data": {'percent': 0, 'step': 'Starting...', 'events': 0}, "status": "unknown"})


@app.route("/api/ack/result", methods=["POST"])
@login_required
def ack_job_result():
    data = request.json
    job_id = data.get("jobId", "")
    job = _own_job(ack_jobs, job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    if job['status'] == 'done':
        return jsonify({"success": True, "data": job['result'], "total": len(job['result'])})
    elif job['status'] == 'error':
        return jsonify({"success": False, "error": job.get('error', 'Unknown error')})
    elif job['status'] == 'cancelled':
        return jsonify({"success": False, "error": "Cancelled"})
    return jsonify({"success": False, "error": "Not finished yet"})


@app.route("/api/ack/cancel", methods=["POST"])
@login_required
def ack_job_cancel():
    data = request.json
    job_id = data.get("jobId", "")
    job = _own_job(ack_jobs, job_id)
    if job:
        job['cancelled'] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Job not found"})


def run_ack_job(job_id, mode, group_ids, time_from, time_till, cfg, nobet_cfg=None):
    """
    ACK report — runs in a background thread.
    Same event-fetching logic as api.py run_report_job:
      1. Fetch events day by day (adaptive chunking + pagination)
      2. Fetch recovery events by objectids
      3. Trigger filtering (disabled trigger/item/host, maintenance)
      4. Dependency suppression (active mode only)
      5. Fetch ACK info for the filtered events
      6. Build the result
    """
    try:
        job = ack_jobs[job_id]
        job['status'] = 'running'
        job['progress'] = {'percent': 0, 'step': 'Connecting to Zabbix...', 'events': 0}

        zapi = zapi_for(cfg)
        zapi.login()

        # ═══ STEP 1: FETCH EVENTS ═══
        if mode == "active":
            job['progress'] = {'percent': 5, 'step': 'Fetching active problems...', 'events': 0}
            problems = zapi._post("problem.get", {
                "output": "extend",
                "groupids": group_ids if group_ids else None,
                "recent": True,
                "sortfield": ["eventid"],
                "sortorder": "DESC",
                "limit": 10000
            }) or []
            job['progress'] = {'percent': 30, 'step': f'{len(problems)} active problems fetched', 'events': len(problems)}
        else:
            # === DAY-BY-DAY FETCH — same as api.py run_report_job ===
            time_from_int = int(time_from)
            time_till_int = int(time_till)
            ONE_DAY = 24 * 3600
            time_ranges = []
            t_start = time_from_int
            while t_start < time_till_int:
                t_end = min(t_start + ONE_DAY, time_till_int)
                time_ranges.append((t_start, t_end))
                t_start = t_end

            total_ranges = len(time_ranges)
            BATCH_SIZE = 50
            if group_ids:
                group_batches = [group_ids[i:i+BATCH_SIZE] for i in range(0, len(group_ids), BATCH_SIZE)]
            else:
                group_batches = [None]

            all_events = {}  # eventid -> event (dedup)

            for range_idx, (range_from, range_till) in enumerate(time_ranges):
                if job.get('cancelled'):
                    job['status'] = 'cancelled'; job['progress'] = {'percent': 0, 'step': 'Cancelled', 'events': 0}; return

                from_dt = datetime.fromtimestamp(range_from).strftime('%d.%m.%Y')
                till_dt = datetime.fromtimestamp(range_till).strftime('%d.%m.%Y')

                for batch_idx, batch in enumerate(group_batches):
                    if job.get('cancelled'):
                        job['status'] = 'cancelled'; return

                    batch_label = f" Group {batch_idx+1}/{len(group_batches)}" if len(group_batches) > 1 else ""
                    base_pct = 5 + int((range_idx / max(total_ranges, 1)) * 30)
                    job['progress'] = {'percent': base_pct, 'step': f'Fetching events: {from_dt} - {till_dt}{batch_label}', 'events': len(all_events)}

                    # Fetch with pagination
                    params = {
                        'output': 'extend',
                        'time_from': str(range_from), 'time_till': str(range_till),
                        'source': 0, 'object': 0, 'value': '1',
                        'sortfield': ['eventid'], 'sortorder': 'ASC', 'limit': 10000
                    }
                    if batch:
                        params['groupids'] = batch

                    try:
                        page = zapi._post("event.get", params) or []
                        results = list(page)
                        if len(results) >= 10000:
                            while len(page) >= 10000:
                                last_clock = page[-1].get('clock', str(range_from))
                                next_params = dict(params); next_params['time_from'] = last_clock
                                time_module.sleep(0.5)
                                page = zapi._post("event.get", next_params) or []
                                if not page: break
                                existing_ids = set(r['eventid'] for r in results)
                                new_evs = [e for e in page if e['eventid'] not in existing_ids]
                                results.extend(new_evs)
                                if not new_evs: break
                        for ev in results:
                            all_events[ev['eventid']] = ev
                    except Exception as e:
                        app.logger.warning(f"ACK job event fetch error {from_dt}: {e}")

                    if len(group_batches) > 1:
                        time_module.sleep(0.5)

            problems = list(all_events.values())
            job['progress'] = {'percent': 35, 'step': f'{len(problems)} events fetched, checking recoveries...', 'events': len(problems)}

        # ═══ STEP 2: RECOVERY EVENTS (history mode) ═══
        r_eventid_map = {}
        if mode == "history" and problems:
            r_eventids = [p.get("r_eventid") for p in problems if p.get("r_eventid") and p.get("r_eventid") != "0"]
            if r_eventids:
                for i in range(0, len(r_eventids), 500):
                    batch = r_eventids[i:i+500]
                    rev_events = zapi._post("event.get", {"output": ["eventid", "clock"], "eventids": batch}) or []
                    for ev in rev_events:
                        r_eventid_map[ev["eventid"]] = int(ev["clock"])

        # ═══ STEP 3: TRIGGER FILTERING ═══
        job['progress'] = {'percent': 40, 'step': 'Fetching trigger details...', 'events': len(problems)}

        trigger_ids = list({p.get("objectid", "") for p in problems if p.get("objectid")})
        trigger_map = {}
        trigger_info_map = {}
        disabled_triggers = set()
        maintenance_triggers = set()

        if trigger_ids:
            for i in range(0, len(trigger_ids), 500):
                if job.get('cancelled'):
                    job['status'] = 'cancelled'; return
                batch = trigger_ids[i:i+500]
                triggers = zapi._post("trigger.get", {
                    "triggerids": batch,
                    "selectHosts": ["hostid", "name", "status", "maintenance_status"],
                    "selectItems": ["itemid", "status"],
                    "selectDependencies": ["triggerid", "status"],
                    "output": ["triggerid", "description", "status"]
                }) or []
                t_pct = 40 + int(((i + 500) / max(len(trigger_ids), 1)) * 15)
                job['progress'] = {'percent': min(t_pct, 55), 'step': f'Trigger details: {min(i+500, len(trigger_ids))}/{len(trigger_ids)}', 'events': len(problems)}
                for t in triggers:
                    tid = t["triggerid"]
                    trigger_info_map[tid] = t
                    if t.get("status") == "1":
                        disabled_triggers.add(tid); continue
                    items = t.get("items", [])
                    active_items = [it for it in items if it.get("status") == "0"]
                    if items and not active_items:
                        disabled_triggers.add(tid); continue
                    hosts = t.get("hosts", [])
                    active_hosts = [h for h in hosts if h.get("status") == "0"]
                    if not active_hosts:
                        disabled_triggers.add(tid); continue
                    maint_hosts = [h for h in active_hosts if h.get("maintenance_status") == "1"]
                    if len(maint_hosts) == len(active_hosts):
                        maintenance_triggers.add(tid)
                    trigger_map[tid] = t

        # Dependency suppression — only in ACTIVE mode
        suppressed_triggers = set()
        if mode == "active":
            active_problem_triggers = {p['objectid'] for p in problems if p.get('objectid') and p['objectid'] not in disabled_triggers}
            for tid in active_problem_triggers:
                t = trigger_info_map.get(tid)
                if not t: continue
                for dep in t.get('dependencies', []):
                    dep_tid = dep.get('triggerid')
                    if dep_tid in active_problem_triggers and dep.get('status') != '1':
                        suppressed_triggers.add(tid); break

        # ═══ EVENT FILTERING ═══
        job['progress'] = {'percent': 58, 'step': 'Filtering events...', 'events': len(problems)}
        filtered_problems = []
        for p in problems:
            trigger_id = p.get("objectid", "")
            if trigger_id in disabled_triggers: continue
            if trigger_id in suppressed_triggers: continue
            if trigger_id in maintenance_triggers:
                if mode == "active":
                    continue
                else:
                    r_clock_v = p.get("r_clock", "0")
                    r_eid_v = p.get("r_eventid", "0")
                    if (not r_clock_v or r_clock_v == "0") and (not r_eid_v or r_eid_v == "0"):
                        continue
            filtered_problems.append(p)

        app.logger.info(
            f"[ACK job] mode={mode} raw={len(problems)} disabled={len(disabled_triggers)} "
            f"maintenance={len(maintenance_triggers)} suppressed={len(suppressed_triggers)} "
            f"filtered={len(filtered_problems)}"
        )

        # ═══ STEP 4: FETCH ACK INFO (only for filtered events) ═══
        job['progress'] = {'percent': 60, 'step': f'Fetching ACK details... ({len(filtered_problems)} events)', 'events': len(filtered_problems)}
        filtered_event_ids = [p["eventid"] for p in filtered_problems]
        ack_map = {}

        if filtered_event_ids:
            ACK_BATCH = 500
            for i in range(0, len(filtered_event_ids), ACK_BATCH):
                if job.get('cancelled'):
                    job['status'] = 'cancelled'; return
                batch_ids = filtered_event_ids[i:i+ACK_BATCH]
                ack_events = zapi._post("event.get", {
                    "output": ["eventid"],
                    "eventids": batch_ids,
                    "selectAcknowledges": "extend"
                }) or []
                for ae in ack_events:
                    ack_map[ae["eventid"]] = ae.get("acknowledges", [])
                a_pct = 60 + int(((i + ACK_BATCH) / max(len(filtered_event_ids), 1)) * 20)
                job['progress'] = {'percent': min(a_pct, 80), 'step': f'ACK details: {min(i+ACK_BATCH, len(filtered_event_ids))}/{len(filtered_event_ids)}', 'events': len(filtered_problems)}

        # ═══ STEP 5: USER NAMES ═══
        job['progress'] = {'percent': 82, 'step': 'Fetching user details...', 'events': len(filtered_problems)}
        all_user_ids = set()
        for acks in ack_map.values():
            for a in acks:
                uid = a.get("userid")
                if uid: all_user_ids.add(str(uid))

        user_map = {}
        if all_user_ids:
            users = zapi._post("user.get", {"output": ["userid", "username", "name", "surname"], "userids": list(all_user_ids)}) or []
            for u in users:
                username = u.get("username", "").strip()
                name = u.get("name", "").strip()
                surname = u.get("surname", "").strip()
                full = f"{name} {surname}".strip()
                user_map[str(u["userid"])] = username or full or f"User {u['userid']}"

        zapi.logout()

        # ═══ STEP 6: BUILD RESULT ═══
        job['progress'] = {'percent': 85, 'step': 'Preparing results...', 'events': len(filtered_problems)}
        result = []
        total = len(filtered_problems)
        for idx, p in enumerate(filtered_problems):
            trigger_id = p.get("objectid", "")
            clock = int(p.get("clock", 0))
            acks = ack_map.get(p["eventid"], [])
            trigger_info = trigger_map.get(trigger_id, {})
            hosts = trigger_info.get("hosts", p.get("hosts", []))
            active_hosts = [h for h in hosts if h.get("status") == "0"]
            host_name = active_hosts[0]["name"] if active_hosts else (hosts[0]["name"] if hosts else "Unknown")

            r_eventid = p.get("r_eventid", "0")
            r_clock_val = int(p.get("r_clock") or 0)
            if r_clock_val == 0 and r_eventid and r_eventid != "0":
                r_clock_val = r_eventid_map.get(r_eventid, 0)
            clear_sec = (r_clock_val - clock) if r_clock_val > 0 else -1

            sorted_acks = sorted(acks, key=lambda a: int(a.get("clock", 0)))
            all_acks_list = [{"clock": int(a.get("clock", 0)), "username": user_map.get(str(a.get("userid", "")), f"User {a.get('userid','')}"), "message": a.get("message", "")} for a in sorted_acks]

            if sorted_acks:
                first = sorted_acks[0]
                ack_clock = int(first.get("clock", 0))
                ack_delay_sec = ack_clock - clock
                ack_username = user_map.get(str(first.get("userid", "")), f"User {first.get('userid','')}")
                ack_message = first.get("message", "")
                ack_status = "ack"
            else:
                ack_clock = 0; ack_delay_sec = -1; ack_username = ""; ack_message = ""; ack_status = "noack"

            result.append({
                "eventid": p["eventid"], "clock": clock, "r_clock": r_clock_val,
                "clear_sec": clear_sec, "name": p.get("name", trigger_info.get("description", "")),
                "severity": p.get("severity", "0"), "host": host_name,
                "ack_status": ack_status, "ack_clock": ack_clock,
                "ack_delay_sec": ack_delay_sec, "ack_username": ack_username,
                "ack_message": ack_message, "ack_count": len(acks), "all_acks": all_acks_list,
                "nobetci": nobetci_for(clock, nobet_cfg)  # on-call person at the moment of the alarm
            })

            if idx % 500 == 0 and total > 0:
                job['progress'] = {'percent': 85 + int((idx / total) * 12), 'step': f'Processing data... ({idx}/{total})', 'events': total}

        job['progress'] = {'percent': 100, 'step': f'Report ready! ({len(result)} alarms)', 'events': len(result)}
        job['result'] = result
        job['status'] = 'done'

    except Exception as e:
        job = ack_jobs.get(job_id, {})
        job['status'] = 'error'
        job['error'] = str(e)
        job['progress'] = {'percent': 0, 'step': f'Error: {str(e)[:100]}', 'events': 0}


@app.route("/api/ack/hostgroups", methods=["POST"])
@login_required
def get_ack_hostgroups():
    cfg = get_active_server_config()
    if not cfg:
        return _no_active_server_response()
    try:
        zapi = zapi_for(cfg)
        zapi.login()
        groups = zapi._post("hostgroup.get", {
            "output": ["groupid", "name"],
            "selectHosts": ["hostid"],
            "sortfield": "name"
        }) or []
        zapi.logout()
        for g in groups:
            g["host_count"] = len(g.get("hosts", []))
        return jsonify({"success": True, "data": groups})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── USER MANAGEMENT ─────────────────────────────────
@app.route('/users')
@login_required
def users_page():
    if not current_user.is_admin(): return redirect(url_for('dashboard'))
    return render_template('users.html', users=User.query.order_by(User.created_at.desc()).all())

@app.route('/users/create', methods=['POST'])
@login_required
def create_user():
    if not current_user.is_admin(): return redirect(url_for('dashboard'))
    username = request.form.get('username','').strip()
    password = request.form.get('password','').strip()
    role = request.form.get('role','user')
    if not username or not password:
        flash('Username and password are required!', 'error')
    elif role not in ROLES:
        flash('Unknown role.', 'error')
    elif User.query.filter_by(username=username).first():
        flash(f'"{username}" already exists!', 'error')
    elif password_problem(password):
        flash(password_problem(password), 'error')
    else:
        # The admin knows this password, so the user replaces it at first sign-in
        u = User(username=username, role=role, must_change_password=True)
        u.set_password(password)
        sdb.session.add(u)
        sdb.session.commit()
        flash(f'"{username}" created.', 'success')
    return redirect(url_for('users_page'))


@app.route('/users/reset/<int:uid>', methods=['POST'])
@login_required
def reset_user_password(uid):
    """Admin sets a temporary password; the user must change it at next sign-in."""
    if not current_user.is_admin(): return redirect(url_for('dashboard'))
    u = User.query.get_or_404(uid)
    password = request.form.get('password','').strip()
    if password_problem(password):
        flash(password_problem(password), 'error')
    else:
        u.set_password(password)
        u.must_change_password = True
        sdb.session.commit()
        flash(f'Password of "{u.username}" reset.', 'success')
    return redirect(url_for('users_page'))

@app.route("/alarm/cache")
@login_required
def alarm_cache_view():
    return render_template("alarm_cache.html")


@app.route("/alarm/api/cache-browse", methods=["POST"])
@login_required
def alarm_cache_browse():
    """List data from the cache — with date range + search + pagination"""
    data = request.json
    tf = data.get("timeFrom", 0)
    tt = data.get("timeTill", int(time_module.time()))
    search = (data.get("search") or "").strip().lower()
    page = max(1, int(data.get("page", 1)))
    per_page = 50
    offset = (page - 1) * per_page

    cfg = get_active_server_config()
    if not cfg:
        return _no_active_server_response()
    scope = str(cfg['id'])

    try:
        conn = cache_db.get_db()
        cur = conn.cursor()

        where = "clock >= ? AND clock <= ? AND eventid LIKE ?"
        params = [tf, tt, f"{scope}#%"]
        if search:
            where += " AND (LOWER(host) LIKE ? OR LOWER(name) LIKE ?)"
            params += [f"%{search}%", f"%{search}%"]

        cur.execute(f"SELECT COUNT(*) FROM enriched_events WHERE {where}", params)
        total = cur.fetchone()[0]

        cur.execute(f"""
            SELECT eventid, clock, r_clock, severity, name, host, resolved, duration, mesai
            FROM enriched_events
            WHERE {where}
            ORDER BY clock DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        rows = []
        for r in cur.fetchall():
            rows.append({
                "eventid": r[0].split('#', 1)[1] if '#' in r[0] else r[0], "clock": r[1], "r_clock": r[2],
                "severity": r[3], "name": r[4], "host": r[5],
                "resolved": bool(r[6]), "duration": r[7], "mesai": mesai_for(r[1])
            })

        # Coverage summary (which days are in cache)
        cur.execute("""
            SELECT DISTINCT day_start, fetched_at FROM cache_coverage
            WHERE day_start >= ? AND day_start <= ? AND group_set_key LIKE ?
            ORDER BY day_start ASC
        """, [tf, tt, f"{scope}:%"])
        coverage = [{"day": c[0], "fetched_at": c[1]} for c in cur.fetchall()]

        cur.close(); conn.close()

        return jsonify({
            "success": True,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
            "data": rows,
            "coverage": coverage
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/users/delete/<int:uid>', methods=['POST'])
@login_required
def delete_user(uid):
    if not current_user.is_admin(): return redirect(url_for('dashboard'))
    u = User.query.get_or_404(uid)
    if u.username == 'admin':
        flash('The admin user cannot be deleted!', 'error')
    else:
        for s in ZabbixServer.query.filter_by(user_id=u.id).all():
            ReportSchedule.query.filter_by(server_id=s.id).delete()
            sdb.session.delete(s)
        ReportSchedule.query.filter_by(user_id=u.id).delete()
        sdb.session.delete(u); sdb.session.commit()
        flash(f'"{u.username}" deleted.', 'success')
    return redirect(url_for('users_page'))


# ─── INVENTORY ───────────────────────────────────────
@app.route('/envanter')
@login_required
def envanter():
    return render_template('envanter.html', locations_json='[]')


TLS_ERROR = ('TLS certificate verification failed. If this server uses a self-signed '
             'certificate, turn off "Verify TLS certificate" in its settings.')


# api url -> (checked_at, True if the token must be sent as a Bearer header)
_bearer_auth = {}
BEARER_CHECK_TTL = 3600


def _uses_bearer_auth(url, verify):
    """Zabbix 6.4 introduced the "Authorization: Bearer" header and 7.2 removed the
    "auth" field of the request body, so the server version decides how the
    session token is sent. Asked once per server and remembered for an hour; if
    the version cannot be read, the older body field is used."""
    known = _bearer_auth.get(url)
    if known and time_module.time() - known[0] < BEARER_CHECK_TTL:
        return known[1]
    bearer = False
    try:
        r = req_lib.post(url, data=json.dumps({"jsonrpc": "2.0", "method": "apiinfo.version", "params": [], "id": 1}),
                         headers={"Content-Type": "application/json"}, verify=verify, timeout=10)
        major, minor = (int(x) for x in str(r.json().get("result", "")).split(".")[:2])
        bearer = (major, minor) >= (6, 4)
    except req_lib.exceptions.SSLError:
        raise Exception(TLS_ERROR) from None
    except Exception:
        return False        # not cached: try again on the next request
    _bearer_auth[url] = (time_module.time(), bearer)
    return bearer


def _zabbix_post(url, payload, verify, timeout):
    """POST a JSON-RPC payload. The server certificate is checked unless the
    server was saved with verification turned off."""
    headers = {"Content-Type": "application/json"}
    if payload.get("auth") and _uses_bearer_auth(url, verify):
        payload = dict(payload)
        headers["Authorization"] = "Bearer " + payload.pop("auth")
    try:
        return req_lib.post(url, data=json.dumps(payload), headers=headers,
                            verify=verify, timeout=timeout)
    except req_lib.exceptions.SSLError:
        raise Exception(TLS_ERROR) from None


class ZabbixAPI:
    def __init__(self, url, user, password, verify=True):
        self.url=url; self.user=user; self.passw=password; self.verify=verify; self.token=None; self._id=1

    def _post(self, method, params=None):
        payload={"jsonrpc":"2.0","method":method,"params":params or {},"id":self._id}
        if self.token and method != "user.login": payload["auth"]=self.token
        self._id += 1
        r = _zabbix_post(self.url, payload, self.verify, 120)
        r.raise_for_status()
        result = r.json()
        if "error" in result: raise Exception(f"API error: {result['error']}")
        return result.get("result")

    def login(self):
        self.token = self._post("user.login", {"username":self.user,"password":self.passw})

    def logout(self):
        if self.token:
            try: self._post("user.logout")
            except: pass
            self.token = None

    def get_all_hosts(self):
        """ALL hosts on the server visible to the user, without a group filter."""
        return self._post("host.get",{
            "output":["hostid","host","name","status"],
            "selectInterfaces":["ip","dns","main","type"],"selectInventory":["serialno_a","serialno_b","tag"],
            "selectGroups":["name"],"sortfield":"name"}) or []

    def get_items_for_hosts(self, host_ids, search_keyword, preferred_names):
        if not host_ids: return {}
        items = self._post("item.get",{
            "output":["name","lastvalue","hostid"],"hostids":list(host_ids),
            "search":{"name":search_keyword},"searchCaseSensitive":False}) or []
        host_items = {}
        for it in items:
            hid=it["hostid"]; val=(it.get("lastvalue") or "").strip()
            if hid not in host_items: host_items[hid]={}
            host_items[hid][it["name"]]=val
        result = {}
        for hid, item_dict in host_items.items():
            chosen=""
            for pref in preferred_names:
                v=item_dict.get(pref,"")
                if v: chosen=v; break
            if not chosen:
                for v in item_dict.values():
                    if v: chosen=v; break
            result[hid]=chosen
        return result


def pick_ip(interfaces):
    for iface in interfaces:
        if iface.get("main")=="1": return iface.get("ip") or iface.get("dns") or ""
    return interfaces[0].get("ip") or interfaces[0].get("dns") or "" if interfaces else ""

def pick_serial_inv(inventory):
    if not inventory: return ""
    return ((inventory.get("serialno_a") or "").strip()
            or (inventory.get("serialno_b") or "").strip()
            or (inventory.get("tag") or "").strip())

def collect_all_hosts(zapi):
    """Collect the fetchable serial numbers of ALL hosts on the server into a
    single list, regardless of group."""
    hosts = zapi.get_all_hosts()
    host_ids = [h["hostid"] for h in hosts]
    serial_map = zapi.get_items_for_hosts(host_ids, "serial", SERIAL_ITEM_NAMES)
    rows = []
    for h in hosts:
        hid = h["hostid"]; inv = h.get("inventory") or {}
        serial = serial_map.get(hid, "") or pick_serial_inv(inv)
        if not serial:                       # skip hosts without a serial
            continue
        grps = [g["name"] for g in h.get("groups", [])]
        rows.append({"lokasyon": grps[0] if grps else "",
                     "serial": serial,
                     "name": h.get("name", "").strip(),
                     "hostname": h.get("host", "").strip(),
                     "ip": pick_ip(h.get("interfaces") or []),
                     "status": "Enabled" if h.get("status") == "0" else "Disabled"})
    rows.sort(key=lambda r: (r["lokasyon"].lower(), r["name"].lower()))
    return {ALL_CAT: rows}


def collect_data(zapi):
    """Collect inventory: a flat serial-number list of all hosts on the server.
    Returns: (data, eksik, meta, err) — meta={'mode','categories'}."""
    return collect_all_hosts(zapi), {}, {"mode": "all", "categories": [ALL_CAT]}, None

def create_excel_bytes(data, eksik, meta, lang="en"):
    L = reporting.labels(lang)
    categories = meta.get("categories", [ALL_CAT])
    TAB_COLORS={ALL_CAT:"4472C4"}
    def _border():
        s=Side(style="thin",color="BFBFBF")
        return Border(left=s,right=s,top=s,bottom=s)
    wb=Workbook(); wb.remove(wb.active); border=_border()
    hdr_fill=PatternFill("solid",fgColor="1F3864")
    hdr_font=Font(color="FFFFFF",bold=True,name="Calibri",size=11)
    hdr_align=Alignment(horizontal="center",vertical="center")
    for cat in categories:
        rows=data.get(cat,[]); ws=wb.create_sheet(title=L["all_devices"] if cat==ALL_CAT else cat)
        ws.sheet_properties.tabColor=TAB_COLORS.get(cat,"808080"); ws.freeze_panes="A2"
        col_headers=[L["location"],L["serial"],L["name"],L["hostname"],L["ip"],L["status"]]
        for ci,col_name in enumerate(col_headers,1):
            cell=ws.cell(row=1,column=ci); cell.value=col_name
            cell.fill=hdr_fill; cell.font=hdr_font; cell.alignment=hdr_align; cell.border=border
        ws.row_dimensions[1].height=22
        if not rows: ws.cell(row=2,column=1).value=L["no_hosts"]; continue
        odd_fill=PatternFill("solid",fgColor="DCE6F2"); empty_fill=PatternFill("solid",fgColor="FFF2CC")
        data_font=Font(name="Calibri",size=10); data_align=Alignment(vertical="center")
        keys=["lokasyon","serial","name","hostname","ip","status"]
        for ri,row_data in enumerate(rows,2):
            is_odd=(ri%2==0)
            for ci,key in enumerate(keys,1):
                val=row_data.get(key) or ""; cell=ws.cell(row=ri,column=ci)
                if key=="status" and val: val=L["enabled"] if val=="Enabled" else L["disabled"]
                cell.value=val; cell.font=data_font; cell.alignment=data_align; cell.border=border
                if val=="": cell.fill=empty_fill
                elif is_odd: cell.fill=odd_fill
        for ci,w in zip(range(1,7),[40,30,38,38,18,12]):
            ws.column_dimensions[get_column_letter(ci)].width=w
    buf=io.BytesIO(); wb.save(buf); buf.seek(0); return buf

@app.route("/api/generate", methods=["POST"])
@login_required
def generate_report():
    cfg = get_active_server_config()
    if not cfg:
        return _no_active_server_response()
    zapi=zapi_for(cfg)
    try:
        zapi.login(); data,eksik,meta,err=collect_data(zapi)
        if err: return jsonify({"success":False,"error":err}),500
        cats=meta["categories"]; summary={}; total=0
        for cat in cats:
            rows=data.get(cat,[]); n=len(rows)
            summary[cat]={"total":n,"serial_found":sum(1 for r in rows if r["serial"]),
                          "enabled":sum(1 for r in rows if r["status"]=="Enabled"),
                          "disabled":sum(1 for r in rows if r["status"]!="Enabled")}
            total+=n
        summary["TOTAL"]=total
        return jsonify({"success":True,"data":data,"eksik":eksik,"summary":summary,
                        "mode":meta["mode"],"categories":cats,
                        "generated_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    except Exception as exc:
        return jsonify({"success":False,"error":str(exc)}),500
    finally:
        zapi.logout()

@app.route("/api/download", methods=["POST"])
@login_required
def download_excel():
    cfg = get_active_server_config()
    if not cfg:
        return _no_active_server_response()
    zapi=zapi_for(cfg)
    try:
        zapi.login(); data,eksik,meta,err=collect_data(zapi)
        if err: return jsonify({"success":False,"error":err}),500
        lang=(request.get_json(silent=True) or {}).get("lang","en")
        buf=create_excel_bytes(data,eksik,meta,lang)
        fname=f"Zabbix_SAN_Inventory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(buf,as_attachment=True,download_name=fname,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as exc:
        return jsonify({"success":False,"error":str(exc)}),500
    finally:
        zapi.logout()


# ─── ALARM REPORT — HELPERS ────────────────────────────
def zapi_for(cfg):
    """ZabbixAPI client for a server config (get_active_server_config / server_config)."""
    return ZabbixAPI(cfg['api_url'], cfg['user'], cfg['password'], cfg.get('verify_tls', True))


def zreq(session, method, params):
    """alarm_zabbix_req for a logged-in alarm session ({'url','auth','verify',...})."""
    return alarm_zabbix_req(session['url'], method, params, session['auth'],
                            verify=session.get('verify', True))


def zabbix_session(cfg):
    """Log in to a server and return the session dict the alarm report code uses.
    Zabbix 5.x names the login field "user", newer versions "username"."""
    try:
        token=alarm_zabbix_req(cfg["base_url"],"user.login",{"username":cfg["user"],"password":cfg["password"]},verify=cfg["verify_tls"])
    except Exception as e:
        if str(e)==TLS_ERROR: raise
        token=alarm_zabbix_req(cfg["base_url"],"user.login",{"user":cfg["user"],"password":cfg["password"]},verify=cfg["verify_tls"])
    return {"url":cfg["base_url"],"auth":token,"scope":str(cfg["id"]),"verify":cfg["verify_tls"]}


def alarm_zabbix_req_url(api_url, method, params, auth=None, timeout=10, verify=True):
    """One-shot request (no retry) to a full api_jsonrpc.php URL — for status checks."""
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    if auth:
        payload["auth"] = auth
    r = _zabbix_post(api_url, payload, verify, timeout)
    r.raise_for_status()
    if not r.text:
        raise Exception(f"Empty response ({method})")
    result = r.json()
    if "error" in result:
        raise Exception(result["error"].get("data") or result["error"].get("message"))
    return result.get("result")


def alarm_zabbix_req(url, method, params, auth=None, max_retries=3, verify=True):
    payload={"jsonrpc":"2.0","method":method,"params":params,"id":1}
    if auth: payload["auth"]=auth
    last_error=None
    for attempt in range(max_retries):
        try:
            r=_zabbix_post(f"{url}/api_jsonrpc.php",payload,verify,300)
            if not r.text: raise Exception(f"Empty response ({method})")
            result=r.json()
            if "error" in result:
                raise Exception(result["error"].get("data") or result["error"].get("message"))
            return result.get("result")
        except req_lib.exceptions.Timeout:
            last_error=f"Timed out ({method}), attempt {attempt+1}/{max_retries}"
        except req_lib.exceptions.ConnectionError:
            last_error=f"Connection error ({method}), attempt {attempt+1}/{max_retries}"
        except Exception as e:
            error_str=str(e)
            # A certificate problem will not fix itself: fail now instead of retrying
            if error_str != TLS_ERROR and any(x in error_str.lower() for x in ['too many','timeout','connection']):
                last_error=f"{error_str} — attempt {attempt+1}/{max_retries}"
            else:
                raise
        time_module.sleep(2**(attempt+1))
    raise Exception(f"API failed after {max_retries} attempts: {last_error}")


def fetch_events_safe(session, t_from, t_till, group_batch, progress_cb=None):
    MIN_CHUNK=3600
    t_from_int=int(t_from); t_till_int=int(t_till)
    chunk_duration=t_till_int-t_from_int
    params={
        'output':'extend','time_from':str(t_from_int),'time_till':str(t_till_int),
        'source':0,'object':0,'value':'1',
        'sortfield':['eventid'],'sortorder':'ASC','limit':10000
    }
    if group_batch: params['groupids']=group_batch
    try:
        page=zreq(session,'event.get',params)
        results=list(page) if page else []
        if len(results)>=10000:
            while len(page)>=10000:
                last_clock=page[-1].get('clock',str(t_from_int))
                next_params=dict(params); next_params['time_from']=last_clock
                time_module.sleep(1)
                page=zreq(session,'event.get',next_params)
                if not page: break
                existing_ids=set(r['eventid'] for r in results)
                new_evs=[e for e in page if e['eventid'] not in existing_ids]
                results.extend(new_evs)
                if not new_evs: break
        return results
    except Exception as e:
        if chunk_duration<=MIN_CHUNK: return []
        mid=t_from_int+chunk_duration//2
        time_module.sleep(2)
        left=fetch_events_safe(session,str(t_from_int),str(mid),group_batch,progress_cb)
        time_module.sleep(1)
        right=fetch_events_safe(session,str(mid),str(t_till_int),group_batch,progress_cb)
        return left+right


def run_report_job(job_id, session, time_from, time_till, group_ids):
    """
    Report generation — same day-by-day fetch as api.py, with cache support.
    Flow:
      1. Cache check → cached days come from the DB
      2. Fetch missing days from Zabbix DAY BY DAY (adaptive chunking + pagination)
      3. Fetch recovery events in a single call by objectids
      4. Trigger filtering (disabled trigger/item/host, maintenance)
      5. Save to cache
      6. Merge cache + fresh data
    """
    try:
        job=report_jobs[job_id]
        job['status']='running'
        job['progress']={'percent':0,'step':'Starting...','events':0}

        time_from_int=int(time_from); time_till_int=int(time_till)
        scope=str(session.get('scope','')) if isinstance(session,dict) else ''

        # 1. CACHE CHECK
        group_set_key=cache_db.compute_group_set_key(group_ids,scope)
        job['progress']={'percent':2,'step':'Checking cache...','events':0}

        all_days=cache_db.get_day_boundaries(time_from_int,time_till_int)
        uncovered_days=cache_db.get_uncovered_days(group_set_key,time_from_int,time_till_int)
        cached_events=cache_db.get_cached_events(group_set_key,time_from_int,time_till_int,scope)
        cached_day_count=len(all_days)-len(uncovered_days)

        if not uncovered_days:
            job['progress']={'percent':100,'step':f'Report ready! (from cache — {len(cached_events)} events)','events':len(cached_events)}
            job['result']=with_business_hours(cached_events); job['status']='done'; return

        if cached_events:
            job['progress']={'percent':5,'step':f'{len(cached_events)} events in cache ({cached_day_count} days). {len(uncovered_days)} days will be fetched from Zabbix...','events':len(cached_events)}
        else:
            job['progress']={'percent':5,'step':f'{len(uncovered_days)} days will be fetched from Zabbix...','events':0}

        # 2. FETCH FROM ZABBIX DAY BY DAY (same as api.py run_report_job)
        # We don't merge consecutive days — each day is a separate API call
        ONE_DAY=24*3600
        day_ranges=[]
        for day_start,_ in uncovered_days:
            day_end=day_start+ONE_DAY
            # Don't exceed the date range
            if day_end > time_till_int:
                day_end = time_till_int
            if day_start < time_from_int:
                day_start = time_from_int
            day_ranges.append((day_start, day_end))

        total_days=len(day_ranges)
        BATCH_SIZE=50
        if group_ids:
            group_batches=[group_ids[i:i+BATCH_SIZE] for i in range(0,len(group_ids),BATCH_SIZE)]
        else:
            group_batches=[None]

        all_events={}  # eventid -> event (dedup)

        for day_idx,(day_from,day_till) in enumerate(day_ranges):
            if job.get('cancelled'):
                job['status']='cancelled'; job['progress']={'percent':0,'step':'Cancelled','events':0}; return

            from_dt=datetime.fromtimestamp(day_from).strftime('%d.%m.%Y')
            base_pct=5+int((day_idx/max(total_days,1))*40)

            for batch_idx,batch in enumerate(group_batches):
                if job.get('cancelled'):
                    job['status']='cancelled'; return

                batch_label=f" Group {batch_idx+1}/{len(group_batches)}" if len(group_batches)>1 else ""
                job['progress']={'percent':base_pct,'step':f'Fetching: {from_dt} ({day_idx+1}/{total_days}){batch_label}','events':len(all_events)}

                batch_events=fetch_events_safe(session,day_from,day_till,batch)
                for ev in batch_events:
                    all_events[ev['eventid']]=ev

                if len(group_batches)>1: time_module.sleep(0.5)

        events=list(all_events.values())

        if not events:
            cache_db.save_events_to_cache([],group_set_key,uncovered_days,scope)
            job['progress']={'percent':100,'step':f'Report ready! ({len(cached_events)} events — from cache)','events':len(cached_events)}
            job['result']=with_business_hours(cached_events); job['status']='done'; return

        # 3. RECOVERY EVENTS — single call with objectids (same as api.py)
        job['progress']={'percent':50,'step':f'Fetching recovery events... ({len(events)} problems)','events':len(events)}

        trigger_ids=list(set([e['objectid'] for e in events if e.get('objectid')]))
        trigger_map={}; disabled_triggers=set(); recovery_map={}

        if events:
            try:
                all_objectids=list(set(e['objectid'] for e in events if e.get('objectid')))
                recovery_events=zreq(session, 'event.get',{
                    'output':['objectid','clock'],'objectids':all_objectids,
                    'source':0,'object':0,'value':'0',
                    'time_from':str(time_from_int),'time_till':str(time_till_int),
                    'sortfield':['clock'],'sortorder':'ASC','limit':50000
                })
                if recovery_events:
                    for rev in recovery_events:
                        oid=rev['objectid']
                        if oid not in recovery_map: recovery_map[oid]=[]
                        recovery_map[oid].append(int(rev['clock']))
            except: pass

        # 4. TRIGGER INFO
        job['progress']={'percent':60,'step':f'Fetching trigger details... ({len(trigger_ids)} triggers)','events':len(events)}

        TRIGGER_BATCH=500
        for i in range(0,len(trigger_ids),TRIGGER_BATCH):
            if job.get('cancelled'):
                job['status']='cancelled'; return
            batch_ids=trigger_ids[i:i+TRIGGER_BATCH]
            triggers=zreq(session, 'trigger.get',{
                'triggerids':batch_ids,
                'selectHosts':['hostid','name','status','maintenance_status'],
                'selectItems':['itemid','status'],
                'output':['triggerid','description','status','priority']
            })
            t_pct=60+int(((i+TRIGGER_BATCH)/max(len(trigger_ids),1))*15)
            job['progress']={'percent':min(t_pct,75),'step':f'Trigger details: {min(i+TRIGGER_BATCH,len(trigger_ids))}/{len(trigger_ids)}','events':len(events)}
            time_module.sleep(0.5)

            for trigger in triggers:
                trigger_id=trigger['triggerid']
                if trigger.get('status')=='1': disabled_triggers.add(trigger_id); continue
                items=trigger.get('items',[])
                if items and not [i2 for i2 in items if i2.get('status')=='0']:
                    disabled_triggers.add(trigger_id); continue
                hosts=trigger.get('hosts',[])
                if hosts:
                    active_hosts=[h for h in hosts if h.get('status')=='0']
                    if not active_hosts: disabled_triggers.add(trigger_id); continue
                    trigger_map[trigger_id]={'hosts':active_hosts,'description':trigger.get('description',''),'priority':trigger.get('priority','0')}
                else:
                    trigger_map[trigger_id]={'hosts':hosts,'description':trigger.get('description',''),'priority':trigger.get('priority','0')}

        # 5. ENRICHMENT — including maintenance check
        job['progress']={'percent':80,'step':f'Enriching events... ({len(events)} events)','events':len(events)}
        new_enriched=[]
        total_events=len(events)
        for idx,event in enumerate(events):
            trigger_id=event.get('objectid')
            if trigger_id in disabled_triggers: continue
            trigger_info=trigger_map.get(trigger_id,{})
            hosts=trigger_info.get('hosts',[])
            host_name=hosts[0]['name'] if hosts else 'Unknown'
            host_maintenance=hosts[0].get('maintenance_status','0') if hosts else '0'
            clock=int(event.get('clock',0))
            r_clock=int(event.get('r_clock',0))
            if r_clock>0:
                resolved=True; duration=r_clock-clock
            elif trigger_id in recovery_map:
                recovery_clocks=sorted(recovery_map[trigger_id])
                recovery_time=next((rc for rc in recovery_clocks if rc>=clock),None)
                if recovery_time: resolved=True; duration=recovery_time-clock
                else: resolved=False; duration=0
            else:
                resolved=False; duration=0

            # Maintenance check: host in maintenance + alarm not cleared -> skip
            if host_maintenance == '1' and not resolved:
                continue

            new_enriched.append({
                'eventid':event.get('eventid'),'clock':clock,'r_clock':r_clock,
                'severity':event.get('severity',trigger_info.get('priority','0')),
                'name':event.get('name',trigger_info.get('description','')),
                'host':host_name,'resolved':resolved,'duration':duration,
                'mesai':mesai_for(clock)
            })
            if idx%500==0 and total_events>0:
                e_pct=80+int((idx/total_events)*10)
                job['progress']={'percent':e_pct,'step':f'Processing data... ({idx}/{total_events})','events':idx}

        # 6. SAVE TO CACHE
        job['progress']={'percent':92,'step':f'Saving to cache... ({len(new_enriched)} events)','events':len(new_enriched)}
        cache_db.save_events_to_cache(new_enriched,group_set_key,uncovered_days,scope)

        # 7. MERGE
        job['progress']={'percent':96,'step':'Merging data...','events':len(cached_events)+len(new_enriched)}
        seen=set(); combined=[]
        for ev in cached_events+new_enriched:
            eid=str(ev['eventid'])
            if eid not in seen: seen.add(eid); combined.append(ev)
        combined.sort(key=lambda x: x['clock'])

        cache_label=f' + {len(cached_events)} from cache' if cached_events else ''
        job['progress']={'percent':100,'step':f'Report ready! ({len(new_enriched)} new{cache_label})','events':len(combined)}
        job['result']=with_business_hours(combined); job['status']='done'

    except Exception as e:
        job=report_jobs.get(job_id,{})
        job['status']='error'; job['error']=str(e)
        job['progress']={'percent':0,'step':f'Error: {str(e)[:100]}','events':0}


def cleanup_old_jobs():
    now=time_module.time()
    expired=[jid for jid,job in report_jobs.items() if now-job.get('created_at',0)>3600]
    for jid in expired: del report_jobs[jid]


# ─── ALARM ROUTES ────────────────────────────────────────
@app.route("/alarm")
@login_required
def alarm_index():
    return redirect(url_for('alarm_report'))

@app.route("/alarm/report")
@login_required
def alarm_report():
    return render_template("alarm_report.html")

@app.route("/alarm/api/auto-login", methods=["POST"])
@login_required
def alarm_auto_login():
    cfg = get_active_server_config()
    if not cfg:
        return _no_active_server_response()
    try:
        sid=str(uuid.uuid4())
        alarm_sessions[sid]=dict(zabbix_session(cfg),user=current_user.id)
        return jsonify({"success":True,"sessionId":sid,"url":cfg["base_url"]})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}),401

@app.route("/alarm/api/logout", methods=["POST"])
@login_required
def alarm_zabbix_logout():
    if _alarm_session(request.json): del alarm_sessions[request.json.get("sessionId")]
    return jsonify({"success":True})

@app.route("/alarm/api/hostgroups", methods=["POST"])
@login_required
def alarm_hostgroups():
    data=request.json; session=_alarm_session(data)
    if not session: return jsonify({"success":False,"error":"Session not found"}),401
    try:
        result=zreq(session, "hostgroup.get",
                                {"output":["groupid","name"],"selectHosts":["hostid"],"sortfield":"name"})
        result=[g for g in result if g.get("name","").lower().startswith("")]
        for g in result: g["hosts"]=len(g.get("hosts",[]))
        return jsonify({"success":True,"data":result})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}),500

@app.route('/alarm/api/problems', methods=['POST'])
@login_required
def alarm_get_problems():
    """
    Fetch active problems — same logic as the reference implementation.
    Filtering rules:
      1. Trigger disabled → skip
      2. All items disabled → skip
      3. All hosts disabled → skip
      4. All active hosts in maintenance mode → skip
      5. Dependency: parent trigger produces an active problem AND is not disabled → suppress
    """
    data = request.json
    session = _alarm_session(data)
    group_ids = data.get('groupIds', [])

    if not session:
        return jsonify({'success': False, 'error': 'Session not found'}), 401

    try:
        # 1) Problem list
        problems = zreq(session, 'problem.get', {
            'output': 'extend',
            'groupids': group_ids if group_ids else None,
            'recent': True,
            'sortfield': ['eventid'],
            'sortorder': 'DESC',
            'limit': 500
        }) or []

        # 2) Collect trigger IDs
        trigger_ids = list({p['objectid'] for p in problems if p.get('objectid')})

        trigger_map = {}
        trigger_info = {}
        disabled_triggers = set()
        maintenance_triggers = set()

        if trigger_ids:
            triggers = zreq(session, 'trigger.get', {
                'triggerids': trigger_ids,
                'selectHosts': ['hostid', 'name', 'status', 'maintenance_status'],
                'selectItems': ['itemid', 'status'],
                'selectDependencies': ['triggerid', 'status'],
                'output': ['triggerid', 'description', 'status', 'priority']
            }) or []

            for trigger in triggers:
                trigger_id = trigger['triggerid']
                trigger_info[trigger_id] = trigger

                # 1. Trigger disabled?
                if trigger.get('status') == '1':
                    disabled_triggers.add(trigger_id)
                    continue

                # 2. All items disabled?
                items = trigger.get('items', [])
                active_items = [i for i in items if i.get('status') == '0']
                if items and not active_items:
                    disabled_triggers.add(trigger_id)
                    continue

                # 3. All hosts disabled?
                active_hosts = [h for h in trigger.get('hosts', []) if h.get('status') == '0']
                if not active_hosts:
                    disabled_triggers.add(trigger_id)
                    continue

                # 4. All active hosts in maintenance mode?
                maint_hosts = [h for h in active_hosts if h.get('maintenance_status') == '1']
                if len(maint_hosts) == len(active_hosts):
                    maintenance_triggers.add(trigger_id)

                trigger_map[trigger_id] = active_hosts

        # 5) Set of (valid) trigger IDs producing active problems
        active_problem_triggers = {
            p['objectid'] for p in problems
            if p.get('objectid') and p['objectid'] not in disabled_triggers
        }

        # 6) Dependency check
        suppressed_triggers = set()
        for trigger_id in active_problem_triggers:
            trigger = trigger_info.get(trigger_id)
            if not trigger:
                continue
            for dep in trigger.get('dependencies', []):
                dep_trigger_id = dep.get('triggerid')
                if dep_trigger_id in active_problem_triggers and dep.get('status') != '1':
                    suppressed_triggers.add(trigger_id)
                    break

        # 7) Final filtering
        filtered_problems = []
        for problem in problems:
            trigger_id = problem.get('objectid')
            if trigger_id in disabled_triggers:
                continue
            if trigger_id in suppressed_triggers:
                continue
            if trigger_id in maintenance_triggers:
                continue
            problem['hosts'] = trigger_map.get(trigger_id, [])
            filtered_problems.append(problem)

        app.logger.info(
            f"[/alarm/api/problems] raw={len(problems)} "
            f"disabled={len(disabled_triggers)} "
            f"maintenance={len(maintenance_triggers)} "
            f"suppressed={len(suppressed_triggers)} "
            f"final={len(filtered_problems)}"
        )

        return jsonify({'success': True, 'data': filtered_problems})

    except Exception as e:
        app.logger.error(f"alarm_get_problems error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/alarm/api/history", methods=["POST"])
@login_required
def alarm_history():
    """
    Fetch historical alarms — same filtering as api.py /api/history:
      1. Disabled trigger → skip
      2. All items disabled → skip
      3. All hosts disabled → skip
      4. Maintenance + alarm not cleared (r_clock=0) → skip
      5. Maintenance + alarm cleared → show
    Filtered data is written to the cache (the old raw-cache logic changed).
    """
    data=request.json; session=_alarm_session(data)
    if not session: return jsonify({"success":False,"error":"Session not found"}),401
    try:
        tf=data.get("timeFrom"); tt=data.get("timeTill"); group_ids=data.get("groupIds",[])
        scope=str(session.get("scope","")) if isinstance(session,dict) else ""
        key=cache_db.compute_group_set_key(group_ids,scope)
        uncovered=cache_db.get_uncovered_days(key,tf,tt)
        if uncovered:
            new_events=[]
            for s,e in cache_db.merge_day_ranges(uncovered):
                evs=zreq(session, "event.get",
                                     {"output":"extend",
                                      "groupids":group_ids if group_ids else None,
                                      "time_from":s,"time_till":e,
                                      "value":1,"source":0,"object":0,
                                      "sortfield":"clock","sortorder":"ASC","limit":50000})
                new_events.extend(evs or [])

            # === TRIGGER FILTERING (api.py /api/history logic) ===
            trigger_ids = list({ev['objectid'] for ev in new_events if ev.get('objectid')})
            disabled_triggers = set()
            maintenance_triggers = set()
            trigger_host_map = {}

            TRIGGER_BATCH = 500
            for i in range(0, len(trigger_ids), TRIGGER_BATCH):
                batch_ids = trigger_ids[i:i+TRIGGER_BATCH]
                triggers = zreq(session, 'trigger.get', {
                    'triggerids': batch_ids,
                    'selectHosts': ['hostid', 'name', 'status', 'maintenance_status'],
                    'selectItems': ['itemid', 'status'],
                    'output': ['triggerid', 'status']
                }) or []

                for trigger in triggers:
                    tid = trigger['triggerid']

                    # 1. Trigger disabled?
                    if trigger.get('status') == '1':
                        disabled_triggers.add(tid)
                        continue

                    # 2. All items disabled?
                    items = trigger.get('items', [])
                    if items:
                        active_items = [it for it in items if it.get('status') == '0']
                        if not active_items:
                            disabled_triggers.add(tid)
                            continue

                    # 3. All hosts disabled?
                    hosts = trigger.get('hosts', [])
                    if hosts:
                        active_hosts = [h for h in hosts if h.get('status') == '0']
                        if not active_hosts:
                            disabled_triggers.add(tid)
                            continue
                        trigger_host_map[tid] = active_hosts

                        # 4. All active hosts in maintenance mode?
                        maint_hosts = [h for h in active_hosts if h.get('maintenance_status') == '1']
                        if len(maint_hosts) == len(active_hosts):
                            maintenance_triggers.add(tid)
                    else:
                        trigger_host_map[tid] = hosts

            # === EVENT FILTERING ===
            filtered_events = []
            for ev in new_events:
                tid = ev.get('objectid')
                if tid in disabled_triggers:
                    continue
                # Skip if in maintenance + alarm not cleared
                if tid in maintenance_triggers:
                    r_clock = ev.get('r_clock', '0')
                    if not r_clock or r_clock == '0':
                        continue
                ev['hosts'] = trigger_host_map.get(tid, [])
                filtered_events.append(ev)

            cache_db.save_events_to_cache(filtered_events, key, uncovered, scope)

        cached=with_business_hours(cache_db.get_cached_events(key,tf,tt,scope))
        return jsonify({"success":True,"data":cached,"fromCache":not bool(uncovered)})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}),500

@app.route("/alarm/api/report-data", methods=["POST"])
@login_required
def alarm_report_data():
    data=request.json; session=_alarm_session(data)
    if not session: return jsonify({"success":False,"error":"Session not found"}),401
    time_from=data.get("timeFrom"); time_till=data.get("timeTill"); group_ids=data.get("groupIds",[])
    job_id=str(uuid.uuid4())
    report_jobs[job_id]={
        'status':'starting',
        'progress':{'percent':0,'step':'Starting...','events':0},
        'result':None,'error':None,'cancelled':False,
        'created_at':time_module.time(),'user':current_user.id
    }
    thread=threading.Thread(target=run_report_job,args=(job_id,session,time_from,time_till,group_ids),daemon=True)
    thread.start()
    cleanup_old_jobs()
    return jsonify({"success":True,"jobId":job_id,"message":"Report generation started"})

@app.route("/alarm/api/report-progress", methods=["POST"])
@login_required
def alarm_report_progress():
    data=request.json; job_id=data.get("jobId","")
    job=_own_job(report_jobs,job_id)
    if job:
        return jsonify({"success":True,"data":job['progress'],"status":job['status'],"error":job.get('error')})
    return jsonify({"success":True,"data":{'percent':0,'step':'Starting...','events':0},"status":"unknown"})

@app.route("/alarm/api/report-result", methods=["POST"])
@login_required
def alarm_report_result():
    data=request.json; job_id=data.get("jobId","")
    job=_own_job(report_jobs,job_id)
    if not job:
        return jsonify({"success":False,"error":"Job not found"}),404
    if job['status']=='done':
        return jsonify({"success":True,"data":job['result']})
    elif job['status']=='error':
        return jsonify({"success":False,"error":job.get('error','Unknown error')})
    elif job['status']=='cancelled':
        return jsonify({"success":False,"error":"Report cancelled"})
    else:
        return jsonify({"success":False,"error":"Report not finished yet"})

@app.route("/alarm/api/report-cancel", methods=["POST"])
@login_required
def alarm_report_cancel():
    data=request.json; job_id=data.get("jobId","")
    job=_own_job(report_jobs,job_id)
    if job:
        job['cancelled']=True
        return jsonify({"success":True,"message":"Cancelling report..."})
    return jsonify({"success":False,"error":"Job not found"})

@app.route("/alarm/api/cache-stats", methods=["POST"])
@login_required
def alarm_cache_stats():
    cfg = get_active_server_config()
    if not cfg:
        return _no_active_server_response()
    try: return jsonify({"success":True,"data":cache_db.get_cache_stats(str(cfg['id']))})
    except Exception as e: return jsonify({"success":False,"error":str(e)}),500

@app.route("/alarm/api/cache-clear", methods=["POST"])
@login_required
@write_required
def alarm_cache_clear():
    """Clear the cached events of the active server (other servers keep theirs)."""
    cfg = get_active_server_config()
    if not cfg:
        return _no_active_server_response()
    try: cache_db.clear_cache(str(cfg['id'])); return jsonify({"success":True})
    except Exception as e: return jsonify({"success":False,"error":str(e)}),500

@app.route("/alarm/api/sla-data", methods=["POST"])
@login_required
def alarm_sla_data():
    data=request.json; session=_alarm_session(data)
    if not session: return jsonify({"success":False,"error":"Session not found"}),401
    try:
        tf=data.get("timeFrom"); tt=data.get("timeTill")
        slas=zreq(session, "sla.get",{"output":"extend","selectServiceTags":"extend"})
        if not slas: return jsonify({"success":True,"data":[]})
        services=zreq(session, "service.get",{"output":["serviceid","name","status"],"selectTags":"extend"})
        svc_map={s["serviceid"]:s for s in services}
        result=[]
        for sla in slas:
            try:
                sli_data=zreq(session, "sla.getsli",
                                          {"slaid":sla["slaid"],"period_from":int(tf),"period_to":int(tt)})
                if not sli_data: continue
                for s_idx,sid in enumerate(sli_data.get("serviceids",[])):
                    total_sli=0; cnt=0
                    for p in sli_data.get("sli",[]):
                        if s_idx<len(p): total_sli+=float(p[s_idx].get("sli",0)); cnt+=1
                    avg=total_sli/cnt if cnt>0 else 0; slo=float(sla.get("slo",99.9))
                    result.append({"slaName":sla.get("name"),"serviceName":svc_map.get(sid,{}).get("name",sid),
                                   "slo":slo,"sli":round(avg,4),"status":"ok" if avg>=slo else "violation"})
            except: pass
        return jsonify({"success":True,"data":result})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}),500


@app.route("/tkodu")
@login_required
def tkodu_page():
    return render_template("tkodu.html")


@app.route("/api/tkodu", methods=["POST"])
@login_required
def get_tkodu():
    """Fetch all triggers whose name contains the text typed by the user."""
    code = ((request.json or {}).get("code") or "").strip()
    if not code:
        return jsonify({"success": False, "error": "The search text cannot be empty."}), 400
    cfg = get_active_server_config()
    if not cfg:
        return _no_active_server_response()
    try:
        zapi = zapi_for(cfg)
        zapi.login()
        triggers = zapi._post("trigger.get", {
            "output": ["triggerid", "description", "status", "priority"],
            "selectHosts": ["hostid", "name", "status"],
            "search": {"description": code},
            "searchCaseSensitive": False,
            "limit": 5000
        }) or []
        zapi.logout()

        result = []
        for t in triggers:
            hosts = t.get("hosts", [])
            result.append({
                "triggerid": t["triggerid"],
                "description": t["description"],
                "status": "Enabled" if t["status"] == "0" else "Disabled",
                "priority": t["priority"],
                "hosts": [{"name": h["name"], "status": "Enabled" if h["status"] == "0" else "Disabled"} for h in hosts]
            })

        return jsonify({"success": True, "data": result, "total": len(result)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── SCHEDULED REPORTS ───────────────────────────────────
# The alarm report of a server, built on a schedule and e-mailed (HTML summary
# with a comparison to the previous period + the alarm list as an Excel file).
# A background thread (start_scheduler) checks for due schedules; the report
# itself comes from run_report_job, so it uses and fills the same cache as the
# Alarm Report page.
DEFAULT_SEVERITIES = ['2', '3', '4', '5']   # Warning and above, as on the report page


class SmtpConfig(sdb.Model):
    """Outgoing mail server (shared, single row id=1; admin managed)."""
    __tablename__ = 'smtp_config'
    id           = sdb.Column(sdb.Integer, primary_key=True)
    host         = sdb.Column(sdb.String(200), default='')
    port         = sdb.Column(sdb.Integer, default=587)
    security     = sdb.Column(sdb.String(10), default='starttls')   # starttls | ssl | none
    username     = sdb.Column(sdb.String(200), default='')
    password_enc = sdb.Column(sdb.Text, default='')                 # stored encrypted
    from_addr    = sdb.Column(sdb.String(200), default='')


class ReportSchedule(sdb.Model):
    __tablename__ = 'report_schedules'
    id          = sdb.Column(sdb.Integer, primary_key=True)
    user_id     = sdb.Column(sdb.Integer, sdb.ForeignKey('users.id'), nullable=False, index=True)
    server_id   = sdb.Column(sdb.Integer, sdb.ForeignKey('zabbix_servers.id'), nullable=False)
    name        = sdb.Column(sdb.String(120), nullable=False)
    frequency   = sdb.Column(sdb.String(10), nullable=False, default='weekly')   # daily | weekly | monthly
    hour        = sdb.Column(sdb.Integer, nullable=False, default=8)             # local hour of the run
    weekday     = sdb.Column(sdb.Integer, nullable=False, default=0)             # weekly: 0 = Monday
    monthday    = sdb.Column(sdb.Integer, nullable=False, default=1)             # monthly: 1–31
    group_ids   = sdb.Column(sdb.Text, default='[]')     # JSON list; empty = all host groups
    group_names = sdb.Column(sdb.Text, default='[]')     # JSON list, for display
    severities  = sdb.Column(sdb.Text, default='[]')     # JSON list of severity codes
    recipients  = sdb.Column(sdb.Text, nullable=False, default='')
    lang        = sdb.Column(sdb.String(5), default='en')
    enabled     = sdb.Column(sdb.Boolean, nullable=False, default=True)
    next_run    = sdb.Column(sdb.Integer, default=0)     # unix time
    last_run    = sdb.Column(sdb.Integer, default=0)
    last_status = sdb.Column(sdb.String(300), default='')
    created_at  = sdb.Column(sdb.DateTime, default=datetime.utcnow)


def _json_list(text):
    try:
        value = json.loads(text or '[]')
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def smtp_settings():
    """SMTP settings as the dict reporting.send_mail() takes, or None if unset."""
    c = SmtpConfig.query.get(1)
    if not c or not c.host or not c.from_addr:
        return None
    password = ''
    if c.password_enc:
        try:
            password = decrypt_secret(c.password_enc, app.config['SECRET_KEY'])
        except Exception:
            password = ''
    return {'host': c.host, 'port': c.port or 587, 'security': c.security or 'starttls',
            'username': c.username or '', 'password': password, 'from_addr': c.from_addr}


def _next_run_ts(sch, after=None):
    return int(reporting.next_run(sch.frequency, sch.hour, sch.weekday, sch.monthday, after).timestamp())


def _fetch_report_events(zsession, time_from, time_till, group_ids):
    """Run the alarm report job in this thread and return its rows."""
    job_id = str(uuid.uuid4())
    report_jobs[job_id] = {'status': 'starting', 'progress': {}, 'result': None, 'error': None,
                           'cancelled': False, 'created_at': time_module.time()}
    try:
        run_report_job(job_id, zsession, time_from, time_till, group_ids)
        job = report_jobs[job_id]
        if job['status'] != 'done':
            raise Exception(job.get('error') or 'The report job did not finish.')
        return job['result'] or []
    finally:
        report_jobs.pop(job_id, None)


def _send_scheduled_report(sch, run_at):
    smtp = smtp_settings()
    if not smtp:
        raise Exception('SMTP is not configured.')
    recipients, _ = reporting.parse_recipients(sch.recipients)
    if not recipients:
        raise Exception('No recipients.')
    server = ZabbixServer.query.get(sch.server_id)
    if not server:
        raise Exception('Server not found.')
    cfg = server_config(server)
    time_from, time_till, prev_from, prev_till = reporting.period_for(sch.frequency, run_at)
    group_ids = [str(g) for g in _json_list(sch.group_ids)]
    severities = set(str(x) for x in _json_list(sch.severities)) or set(DEFAULT_SEVERITIES)

    zsession = zabbix_session(cfg)
    try:
        events = _fetch_report_events(zsession, time_from, time_till, group_ids)
        previous = _fetch_report_events(zsession, prev_from, prev_till, group_ids)
    finally:
        try:
            alarm_zabbix_req_url(cfg['api_url'], 'user.logout', {}, auth=zsession['auth'],
                                 timeout=6, verify=cfg['verify_tls'])
        except Exception:
            pass
    events = [e for e in events if str(e.get('severity', '0')) in severities]
    previous = [e for e in previous if str(e.get('severity', '0')) in severities]

    lang = sch.lang if sch.lang in reporting.LABELS else 'en'
    L = reporting.labels(lang)
    meta = {'server': server.name, 'groups': ', '.join(str(n) for n in _json_list(sch.group_names)),
            'time_from': time_from, 'time_till': time_till,
            'prev_from': prev_from, 'prev_till': prev_till}
    cur, prev = reporting.summarize(events), reporting.summarize(previous)
    day = lambda ts: datetime.fromtimestamp(ts).strftime('%d.%m.%Y')
    span = day(time_from) if day(time_from) == day(time_till) else f'{day(time_from)} – {day(time_till)}'
    subject = f'{L["report"]} — {server.name} — {L["freq"][sch.frequency]} ({span})'
    filename = f'Alarm_Report_{datetime.fromtimestamp(time_from):%Y%m%d}_{datetime.fromtimestamp(time_till):%Y%m%d}'
    attachments = [(filename + '.xlsx', reporting.build_excel(meta, cur, prev, events, lang))]
    pdf = reporting.build_pdf(meta, cur, prev, lang)
    if pdf:
        attachments.append((filename + '.pdf', pdf))
    reporting.send_mail(smtp, recipients, subject,
                        reporting.build_email_html(meta, cur, prev, lang), attachments)
    return f'Sent to {len(recipients)} recipients — {cur["total"]} alarms'


_schedule_lock = threading.Lock()   # one scheduled report at a time


def run_schedule(schedule_id, run_at=None):
    """Build and e-mail one scheduled report; the outcome is stored on the row."""
    with _schedule_lock, app.app_context():
        sch = ReportSchedule.query.get(schedule_id)
        if not sch:
            return
        try:
            status = _send_scheduled_report(sch, run_at or datetime.now())
        except Exception as e:
            sdb.session.rollback()
            status = f'Error: {str(e)[:250]}'
            app.logger.error(f'[schedule {schedule_id}] {e}')
        sch.last_run = int(time_module.time())
        sch.last_status = status
        sdb.session.commit()


def _scheduler_loop():
    while True:
        try:
            with app.app_context():
                due = ReportSchedule.query.filter(
                    ReportSchedule.enabled.is_(True),
                    ReportSchedule.next_run <= int(time_module.time())).all()
                due_ids = []
                for sch in due:
                    sch.next_run = _next_run_ts(sch)   # claim it before the slow part
                    due_ids.append(sch.id)
                sdb.session.commit()
            for schedule_id in due_ids:
                run_schedule(schedule_id)
        except Exception as e:
            app.logger.error(f'[scheduler] {e}')
        time_module.sleep(30)


_scheduler_started = False


def start_scheduler():
    """Start the background thread that sends due reports (once per process).
    A report missed while the app was not running is sent at the next start."""
    global _scheduler_started
    if _scheduler_started or os.environ.get('ZRS_NO_SCHEDULER') == '1':
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, daemon=True, name='zrs-scheduler').start()


def _schedule_json(sch, server_names):
    return {
        'id': sch.id, 'name': sch.name, 'serverId': sch.server_id,
        'serverName': server_names.get(sch.server_id, ''),
        'frequency': sch.frequency, 'hour': sch.hour, 'weekday': sch.weekday, 'monthday': sch.monthday,
        'groupIds': _json_list(sch.group_ids), 'groupNames': _json_list(sch.group_names),
        'severities': _json_list(sch.severities) or DEFAULT_SEVERITIES,
        'recipients': sch.recipients, 'lang': sch.lang or 'en', 'enabled': bool(sch.enabled),
        'nextRun': sch.next_run or 0, 'lastRun': sch.last_run or 0, 'lastStatus': sch.last_status or '',
    }


def _apply_schedule_payload(sch, data):
    """Validate the form values and copy them onto the row. Returns an error or None."""
    name = (data.get('name') or '').strip()
    if not name:
        return 'A name is required.'
    server = visible_servers().filter(ZabbixServer.id == data.get('serverId')).first()
    if not server:
        return 'Server not found.'
    frequency = data.get('frequency')
    if frequency not in reporting.FREQUENCIES:
        return 'Unknown frequency.'
    try:
        hour, weekday, monthday = int(data.get('hour', 8)), int(data.get('weekday', 0)), int(data.get('monthday', 1))
    except (ValueError, TypeError):
        return 'The run time is not valid.'
    if not (0 <= hour <= 23 and 0 <= weekday <= 6 and 1 <= monthday <= 31):
        return 'The run time is not valid.'
    valid, invalid = reporting.parse_recipients(data.get('recipients'))
    if invalid:
        return f'Invalid e-mail address: {invalid[0]}'
    if not valid:
        return 'At least one recipient is required.'
    severities = [str(x) for x in (data.get('severities') or []) if str(x) in ('0', '1', '2', '3', '4', '5')]
    if not severities:
        return 'Select at least one severity'
    groups = data.get('groups') if isinstance(data.get('groups'), list) else []
    groups = [g for g in groups if isinstance(g, dict) and g.get('id')]

    sch.name, sch.server_id, sch.frequency = name[:120], server.id, frequency
    sch.hour, sch.weekday, sch.monthday = hour, weekday, monthday
    sch.group_ids = json.dumps([str(g['id']) for g in groups])
    sch.group_names = json.dumps([str(g.get('name') or g['id']) for g in groups], ensure_ascii=False)
    sch.severities = json.dumps(severities)
    sch.recipients = ', '.join(valid)
    sch.lang = data.get('lang') if data.get('lang') in reporting.LABELS else 'en'
    sch.enabled = data.get('enabled', True) is not False
    sch.next_run = _next_run_ts(sch)
    return None


@app.route('/schedules')
@login_required
def schedules_page():
    if not current_user.can_write(): return redirect(url_for('dashboard'))
    return render_template('schedules.html')


@app.route('/api/schedules', methods=['GET'])
@login_required
@write_required
def api_list_schedules():
    servers = visible_servers().order_by(ZabbixServer.created_at.asc()).all()
    names = {s.id: s.name for s in servers}
    schedules = ReportSchedule.query.filter_by(user_id=current_user.id).order_by(ReportSchedule.id.asc()).all()
    return jsonify({
        'success': True, 'isAdmin': current_user.is_admin(),
        'smtpConfigured': smtp_settings() is not None,
        'activeServerId': session.get('active_zabbix_id'),
        'servers': [{'id': s.id, 'name': s.name} for s in servers],
        'schedules': [_schedule_json(x, names) for x in schedules],
    })


@app.route('/api/schedules', methods=['POST'])
@login_required
@write_required
def api_create_schedule():
    sch = ReportSchedule(user_id=current_user.id)
    error = _apply_schedule_payload(sch, request.json or {})
    if error:
        return jsonify({'success': False, 'error': error}), 400
    sdb.session.add(sch)
    sdb.session.commit()
    return jsonify({'success': True, 'id': sch.id})


def _own_schedule(schedule_id):
    return ReportSchedule.query.filter_by(id=schedule_id, user_id=current_user.id).first()


@app.route('/api/schedules/<int:schedule_id>', methods=['POST'])
@login_required
@write_required
def api_update_schedule(schedule_id):
    sch = _own_schedule(schedule_id)
    if not sch:
        return jsonify({'success': False, 'error': 'Schedule not found.'}), 404
    error = _apply_schedule_payload(sch, request.json or {})
    if error:
        sdb.session.rollback()
        return jsonify({'success': False, 'error': error}), 400
    sdb.session.commit()
    return jsonify({'success': True})


@app.route('/api/schedules/<int:schedule_id>/delete', methods=['POST'])
@login_required
@write_required
def api_delete_schedule(schedule_id):
    sch = _own_schedule(schedule_id)
    if not sch:
        return jsonify({'success': False, 'error': 'Schedule not found.'}), 404
    sdb.session.delete(sch)
    sdb.session.commit()
    return jsonify({'success': True})


@app.route('/api/schedules/<int:schedule_id>/run', methods=['POST'])
@login_required
@write_required
def api_run_schedule(schedule_id):
    """Send the report now (in the background); the list shows the outcome."""
    sch = _own_schedule(schedule_id)
    if not sch:
        return jsonify({'success': False, 'error': 'Schedule not found.'}), 404
    if not smtp_settings():
        return jsonify({'success': False, 'error': 'SMTP is not configured.'}), 400
    sch.last_status = 'Running...'
    sdb.session.commit()
    threading.Thread(target=run_schedule, args=(sch.id,), daemon=True).start()
    return jsonify({'success': True})


@app.route('/api/schedules/hostgroups', methods=['POST'])
@login_required
@write_required
def api_schedule_hostgroups():
    server = visible_servers().filter(ZabbixServer.id == (request.json or {}).get('serverId')).first()
    if not server:
        return jsonify({'success': False, 'error': 'Server not found.'}), 404
    zapi = zapi_for(server_config(server))
    try:
        zapi.login()
        groups = zapi._post('hostgroup.get', {'output': ['groupid', 'name'], 'sortfield': 'name'}) or []
        return jsonify({'success': True, 'data': [{'id': g['groupid'], 'name': g['name']} for g in groups]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]}), 500
    finally:
        zapi.logout()


@app.route('/api/smtp', methods=['GET'])
@login_required
@admin_required
def api_get_smtp():
    c = SmtpConfig.query.get(1)
    return jsonify({'success': True, 'config': {
        'host': c.host if c else '', 'port': (c.port if c else 587) or 587,
        'security': (c.security if c else 'starttls') or 'starttls',
        'username': c.username if c else '', 'fromAddr': c.from_addr if c else '',
        'hasPassword': bool(c and c.password_enc)}})


@app.route('/api/smtp', methods=['POST'])
@login_required
@admin_required
def api_save_smtp():
    data = request.json or {}
    host = (data.get('host') or '').strip()
    from_addr = (data.get('fromAddr') or '').strip()
    try:
        port = int(data.get('port') or 587)
    except (ValueError, TypeError):
        port = 0
    if not host or not (1 <= port <= 65535):
        return jsonify({'success': False, 'error': 'SMTP host and a valid port are required.'}), 400
    if len(reporting.parse_recipients(from_addr)[0]) != 1:
        return jsonify({'success': False, 'error': 'The sender address is not valid.'}), 400
    security = data.get('security') if data.get('security') in ('starttls', 'ssl', 'none') else 'starttls'
    c = SmtpConfig.query.get(1)
    if not c:
        c = SmtpConfig(id=1)
        sdb.session.add(c)
    c.host, c.port, c.security, c.from_addr = host, port, security, from_addr
    c.username = (data.get('username') or '').strip()
    # If the password is left blank, the existing password is kept
    if data.get('password'):
        c.password_enc = encrypt_secret(data.get('password'), app.config['SECRET_KEY'])
    elif not c.username:
        c.password_enc = ''
    sdb.session.commit()
    return jsonify({'success': True})


@app.route('/api/smtp/test', methods=['POST'])
@login_required
@admin_required
def api_test_smtp():
    data = request.json or {}
    smtp = smtp_settings()
    if not smtp:
        return jsonify({'success': False, 'error': 'SMTP is not configured.'}), 400
    recipients, invalid = reporting.parse_recipients(data.get('to'))
    if invalid or not recipients:
        return jsonify({'success': False, 'error': 'At least one recipient is required.'}), 400
    L = reporting.labels(data.get('lang'))
    try:
        reporting.send_mail(smtp, recipients, L['test_subject'], f'<p>{L["test_body"]}</p>', timeout=15)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:250]}), 502


# ────────────────────────────────────────────────────────
if __name__ == "__main__":
    create_admin()
    # ZRS_HOST / ZRS_PORT are set when the app runs as a service (e.g. in Docker):
    # no browser is opened and the production WSGI server is used.
    host = os.environ.get("ZRS_HOST", "")
    port = int(os.environ.get("ZRS_PORT", "5050"))
    print(f">> First sign-in: admin / {DEFAULT_ADMIN_PASSWORD} (you will be asked to change it)")
    if host:
        from waitress import serve
        print(f">> App started: http://{host}:{port}")
        serve(app, host=host, port=port, threads=12, channel_timeout=600, ident="ZabbixReportStudio")
    else:
        # Open the browser automatically
        import webbrowser, threading as _threading
        def _open_browser():
            time_module.sleep(1.5)
            webbrowser.open(f"http://localhost:{port}")
        _threading.Thread(target=_open_browser, daemon=True).start()
        print(f">> App started: http://localhost:{port}")
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

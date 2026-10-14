# -*- coding: utf-8 -*-
"""
Build the static demo site that is published on GitHub Pages.

    pip install jinja2
    python demo/build.py                                # writes ./_site
    python demo/build.py ../zabbix-report-studio-demo   # writes into the public demo repo

The output folder is emptied first, except for its .git directory, so the build
can target a checkout of the separate public repo that GitHub Pages serves.

The Flask templates are rendered once with a fake signed-in user, absolute paths
are rewritten to relative ones (a project page is served from /<repo>/, not /),
and demo.js is injected into every page to answer the API calls with sample data.
No Flask app, database or Zabbix server is involved.
"""
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "_site"

# route -> (template, output file)
PAGES = {
    "/":             ("dashboard.html",    "index.html"),
    "/servers":      ("servers.html",      "servers.html"),
    "/alarm/report": ("alarm_report.html", "alarm-report.html"),
    "/ack":          ("ack.html",          "ack.html"),
    "/nobet":        ("nobet.html",        "nobet.html"),
    "/tkodu":        ("tkodu.html",        "tkodu.html"),
    "/envanter":     ("envanter.html",     "envanter.html"),
    "/alarm/cache":  ("alarm_cache.html",  "cache.html"),
    "/users":        ("users.html",        "users.html"),
    "/schedules":    ("schedules.html",    "schedules.html"),
    "/change-password": ("change_password.html", "change-password.html"),
    "/login":        ("login.html",        "login.html"),
}
# routes that only redirect in the real app
REDIRECTS = {"/alarm": "alarm-report.html", "/logout": "login.html"}


class DemoUser:
    def __init__(self, uid, username, role, created_at, must_change_password=False):
        self.id, self.username, self.role = uid, username, role
        self.created_at, self.must_change_password = created_at, must_change_password

    def is_admin(self):
        return self.role == "admin"

    def can_write(self):
        return self.role != "viewer"


USERS = [
    DemoUser(1, "admin",    "admin", datetime(2026, 1, 12, 9, 30)),
    DemoUser(2, "a.yilmaz", "user",  datetime(2026, 2, 3, 14, 5)),
    DemoUser(3, "m.kaya",   "user",  datetime(2026, 2, 3, 14, 12)),
    DemoUser(4, "e.demir",  "admin", datetime(2026, 3, 18, 11, 40)),
    DemoUser(5, "s.celik",  "user",  datetime(2026, 5, 6, 16, 22)),
    DemoUser(6, "noc.screen", "viewer", datetime(2026, 9, 2, 10, 15), must_change_password=True),
]

CONTEXT = {
    "current_user": USERS[0],
    "users": list(reversed(USERS)),
    "active_server": {"id": 1, "name": "Demo Zabbix", "base_url": "https://zabbix.example.com"},
    "now": datetime.now(),
    "locations_json": "[]",
    "forced": False,
    "min_len": 8,
    "get_flashed_messages": lambda **kwargs: [],
}

ABS_ATTR = re.compile(r'\b(href|src|action)="(/(?:[^"/][^"]*)?)"')
unmapped = set()


def relative_target(path):
    if path.startswith("/static/"):
        return path[1:]
    if path in PAGES:
        return PAGES[path][1]
    if path in REDIRECTS:
        return REDIRECTS[path]
    if path.startswith("/users/"):      # form posts, handled by demo.js
        return "#"
    unmapped.add(path)
    return path


def render_pages():
    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(["html"]))
    for template, out_name in PAGES.values():
        html = env.get_template(template).render(**CONTEXT)
        html = ABS_ATTR.sub(lambda m: f'{m.group(1)}="{relative_target(m.group(2))}"', html)
        html, n = re.subn(r"<head[^>]*>", lambda m: m.group(0) + '\n<script src="demo.js"></script>',
                          html, count=1)
        if n != 1:
            sys.exit(f"{template}: no <head> tag to inject demo.js into")
        (OUT / out_name).write_text(html, encoding="utf-8")


def copy_static():
    shutil.copytree(ROOT / "static", OUT / "static")
    for js in (OUT / "static").rglob("*.js"):
        src = js.read_text(encoding="utf-8")
        js.write_text(re.sub(r"(location\.href\s*=\s*)'/'", r"\1'index.html'", src), encoding="utf-8")
    shutil.copy2(Path(__file__).with_name("demo.js"), OUT / "demo.js")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for entry in OUT.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    render_pages()
    copy_static()
    if unmapped:
        sys.exit("Unmapped absolute paths in templates (add them to PAGES/REDIRECTS): "
                 + ", ".join(sorted(unmapped)))
    print(f"Demo built: {len(PAGES)} pages -> {OUT}")


if __name__ == "__main__":
    main()

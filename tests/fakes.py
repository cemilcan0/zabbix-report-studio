# -*- coding: utf-8 -*-
"""In-process stand-ins for the outside world: a Zabbix JSON-RPC API and an SMTP
server. Both listen on a free local port and run in daemon threads."""
import json
import random
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASSWORD = "zbxpass"
TOKEN = "tok123"
GROUPS = [{"groupid": "11", "name": "Linux Servers"}, {"groupid": "12", "name": "Network"}]
HOSTS = [("101", "app-01", "11"), ("102", "app-02", "11"), ("103", "db-01", "11"),
         ("201", "sw-01", "12"), ("202", "fw-01", "12")]
PROBLEMS = [("High CPU utilization", "2"), ("Unavailable by ICMP ping", "4"),
            ("Disk space is low", "3"), ("Informational note", "1")]


class FakeZabbix:
    """Serves ~800 problem events spread over the last 40 days.

    `version` decides how the session token must arrive, like the real API:
    before 6.4 only in the body ("auth"), from 7.2 only as a Bearer header."""

    def __init__(self, version="6.0.0"):
        self.version = version
        self.numeric = tuple(int(x) for x in version.split(".")[:2])
        now = int(time.time())
        rnd = random.Random(7)
        self.triggers = {}      # triggerid -> (description, priority, hostid)
        for i, (hostid, _, _) in enumerate(HOSTS):
            for j, (desc, prio) in enumerate(PROBLEMS):
                self.triggers[str(5000 + i * 10 + j)] = (desc, prio, hostid)
        self.events, self.recovery = [], []
        eventid = 900000
        for _ in range(800):
            triggerid = rnd.choice(list(self.triggers))
            clock = now - rnd.randint(0, 40 * 86400)
            duration = rnd.randint(120, 7200)
            eventid += 2
            resolved = clock + duration < now and rnd.random() < 0.9
            desc, prio, _ = self.triggers[triggerid]
            self.events.append({"eventid": str(eventid), "clock": str(clock), "objectid": triggerid,
                                "severity": prio, "name": desc, "value": "1",
                                "r_eventid": str(eventid + 1) if resolved else "0"})
            if resolved:
                self.recovery.append({"eventid": str(eventid + 1), "clock": str(clock + duration),
                                      "objectid": triggerid})
        self.events.sort(key=lambda e: int(e["eventid"]))

        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                try:
                    result = fake.call(body, self.headers.get("Authorization"))
                    out = {"jsonrpc": "2.0", "result": result, "id": body.get("id")}
                except ValueError as e:
                    out = {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params.", "data": str(e)},
                           "id": body.get("id")}
                raw = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def _check_auth(self, body, header):
        if "auth" in body and self.numeric >= (7, 2):
            raise ValueError('Invalid parameter "/": unexpected parameter "auth".')
        if header and self.numeric < (6, 4):
            raise ValueError("Not authorised.")      # old servers ignore the header
        token = body.get("auth") or (header or "").replace("Bearer ", "")
        if token != TOKEN:
            raise ValueError("Not authorised.")

    def call(self, body, header):
        method, p = body["method"], body.get("params") or {}
        if method == "apiinfo.version":
            return self.version
        if method == "user.login":
            if p.get("password") != PASSWORD:
                raise ValueError("Login name or password is incorrect.")
            return TOKEN
        self._check_auth(body, header)
        if method == "user.logout":
            return True
        if method == "hostgroup.get":
            return [dict(g, hosts=[{"hostid": h[0]} for h in HOSTS if h[2] == g["groupid"]]) for g in GROUPS]
        if method in ("problem.get", "item.get", "sla.get"):
            return []
        if method == "host.get":
            if p.get("countOutput"):
                return str(len(HOSTS))
            group_name = {g["groupid"]: g["name"] for g in GROUPS}
            return [{"hostid": h[0], "host": h[1] + ".example.com", "name": h[1], "status": "0",
                     "interfaces": [{"ip": "10.0.0." + h[0][-1], "dns": "", "main": "1", "type": "1"}],
                     "inventory": {"serialno_a": "SN" + h[0]}, "groups": [{"name": group_name[h[2]]}]}
                    for h in HOSTS]
        if method == "trigger.get":
            ids = p.get("triggerids")
            if ids is None:
                needle = (p.get("search") or {}).get("description", "").lower()
                ids = [t for t in self.triggers if needle in self.triggers[t][0].lower()]
            names = {h[0]: h[1] for h in HOSTS}
            return [{"triggerid": t, "description": self.triggers[t][0], "status": "0",
                     "priority": self.triggers[t][1], "dependencies": [],
                     "hosts": [{"hostid": self.triggers[t][2], "name": names[self.triggers[t][2]],
                                "status": "0", "maintenance_status": "0"}],
                     "items": [{"itemid": "1", "status": "0"}]} for t in ids if t in self.triggers]
        if method == "event.get":
            if "eventids" in p:
                wanted = set(p["eventids"])
                return [{"eventid": e["eventid"], "clock": e["clock"], "acknowledges": []}
                        for e in self.recovery + self.events if e["eventid"] in wanted]
            time_from, time_till = int(p.get("time_from", 0)), int(p.get("time_till", time.time() + 1))
            if str(p.get("value")) == "0":
                wanted = set(p.get("objectids") or [])
                return [{"objectid": r["objectid"], "clock": r["clock"]} for r in self.recovery
                        if r["objectid"] in wanted and time_from <= int(r["clock"]) <= time_till]
            groups = set(p.get("groupids") or [])
            host_group = {h[0]: h[2] for h in HOSTS}
            return [e for e in self.events if time_from <= int(e["clock"]) <= time_till
                    and (not groups or host_group[self.triggers[e["objectid"]][2]] in groups)]
        raise ValueError("unknown method " + method)


class FakeSmtp:
    """Accepts any message without TLS or authentication; raw messages end up in .messages."""

    def __init__(self):
        sink = self
        self.messages = []

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                send = lambda text: self.wfile.write((text + "\r\n").encode())
                send("220 fake ESMTP")
                data, in_data = [], False
                for line in self.rfile:
                    if in_data:
                        if line.rstrip(b"\r\n") == b".":
                            in_data = False
                            sink.messages.append(b"".join(data))
                            data = []
                            send("250 queued")
                        else:
                            data.append(line[1:] if line.startswith(b"..") else line)
                        continue
                    command = line.decode("latin1").strip().upper()
                    if command.startswith(("EHLO", "HELO")):
                        send("250 fake")
                    elif command.startswith("DATA"):
                        in_data = True
                        send("354 go ahead")
                    elif command.startswith("QUIT"):
                        send("221 bye")
                        return
                    else:
                        send("250 ok")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

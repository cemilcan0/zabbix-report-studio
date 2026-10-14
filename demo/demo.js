/* Zabbix Report Studio — static demo backend.
 *
 * Loaded only by the GitHub Pages demo (see demo/build.py). It replaces the Flask
 * API with generated sample data so every page works without a server or a real
 * Zabbix. All hosts, people, addresses and alarms below are made up.
 */
(function () {
  'use strict';

  const realFetch = window.fetch.bind(window);
  const DAY = 86400;
  // Anchored to the current hour so numbers agree across pages in one visit.
  const NOW = Math.floor(Date.now() / 3600000) * 3600;

  // ── Deterministic random ─────────────────────────────────
  function mulberry32(a) {
    return function () {
      a |= 0; a = a + 0x6D2B79F5 | 0;
      let t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  const rnd = mulberry32(20260807);
  const rint = (r, a, b) => a + Math.floor(r() * (b - a + 1));
  function weighted(r, items, weightOf) {
    let total = 0;
    for (const it of items) total += weightOf(it);
    let x = r() * total;
    for (const it of items) { x -= weightOf(it); if (x <= 0) return it; }
    return items[items.length - 1];
  }

  // ── Sample infrastructure ────────────────────────────────
  const GROUPS = [
    { id: '11', name: 'Linux Servers',       kind: 'srv',  hosts: ['app-01', 'app-02', 'app-03', 'app-04', 'web-01', 'web-02', 'web-03', 'mq-01'] },
    { id: '12', name: 'Windows Servers',     kind: 'srv',  hosts: ['win-ad-01', 'win-ad-02', 'win-file-01', 'win-rds-01'] },
    { id: '13', name: 'Databases',           kind: 'db',   hosts: ['db-pg-01', 'db-pg-02', 'db-mysql-01', 'db-mssql-01'] },
    { id: '14', name: 'Network / Switches',  kind: 'net',  hosts: ['sw-core-01', 'sw-core-02', 'sw-dist-01', 'sw-dist-02', 'sw-acc-01', 'sw-acc-02', 'sw-acc-03'] },
    { id: '15', name: 'Network / Firewalls', kind: 'net',  hosts: ['fw-edge-01', 'fw-edge-02', 'vpn-gw-01'] },
    { id: '16', name: 'Virtualization',      kind: 'srv',  hosts: ['esx-01', 'esx-02', 'esx-03', 'esx-04', 'vcenter-01'] },
    { id: '17', name: 'Storage',             kind: 'stor', hosts: ['stor-01', 'stor-02', 'san-sw-01', 'san-sw-02', 'tape-lib-01'] },
    { id: '18', name: 'Web Services',        kind: 'web',  hosts: ['portal', 'api-gateway', 'intranet'] }
  ];
  const NOISY = { 'sw-acc-02': 4, 'app-03': 3, 'db-pg-01': 2.4, 'portal': 2.2 };
  const SERIAL_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789';
  const HOSTS = [];
  GROUPS.forEach((g, gi) => g.hosts.forEach((name, hi) => {
    let serial = { srv: 'CZ', db: 'CZ', net: 'FOC', stor: 'SGH', web: '' }[g.kind];
    for (let i = 0; i < 8; i++) serial += SERIAL_CHARS[rint(rnd, 0, SERIAL_CHARS.length - 1)];
    HOSTS.push({
      name, group: g.name, groupId: g.id, kind: g.kind,
      hostname: name + '.example.com',
      ip: '10.20.' + (gi + 1) + '.' + (10 + hi),
      serial: g.kind === 'web' ? '' : serial,
      enabled: rnd() > 0.07,
      weight: NOISY[name] || 0.4 + rnd() * 1.4
    });
  }));

  const PROBLEMS = [
    { n: 'High CPU utilization (over 90% for 5m)',          s: '2', k: ['srv', 'db'],                w: 14,  d: [300, 2400] },
    { n: 'High memory utilization (>90% for 5m)',           s: '3', k: ['srv', 'db'],                w: 9,   d: [600, 5400] },
    { n: 'Unavailable by ICMP ping',                        s: '4', k: ['srv', 'db', 'net', 'stor'], w: 7,   d: [120, 3600] },
    { n: 'High ICMP ping response time',                    s: '2', k: ['net', 'srv'],               w: 8,   d: [120, 1200] },
    { n: '/var: Disk space is critically low (used > 90%)', s: '3', k: ['srv', 'db'],                w: 6,   d: [1800, 28800] },
    { n: '/: Disk space is low (used > 80%)',               s: '2', k: ['srv', 'db'],                w: 8,   d: [3600, 43200] },
    { n: 'Zabbix agent is not available (for 3m)',          s: '3', k: ['srv', 'db'],                w: 6,   d: [180, 2700] },
    { n: 'Interface Gi1/0/{p}: Link down',                  s: '3', k: ['net'],                      w: 9,   d: [60, 7200] },
    { n: 'Host has been restarted (uptime < 10m)',          s: '2', k: ['srv', 'db', 'net'],         w: 4,   d: [600, 900] },
    { n: 'URL check failed: HTTP response code is not 200', s: '4', k: ['web'],                      w: 6,   d: [60, 1800] },
    { n: 'URL response time is too high (> 3s)',            s: '2', k: ['web'],                      w: 7,   d: [120, 2400] },
    { n: 'Power supply PSU2 is in critical state',          s: '5', k: ['net', 'stor', 'srv'],       w: 1.2, d: [1800, 14400] },
    { n: 'Temperature is above critical threshold',         s: '5', k: ['net', 'stor'],              w: 0.8, d: [600, 5400] },
    { n: 'Replication lag is too high (> 60s)',             s: '3', k: ['db'],                       w: 4,   d: [300, 3600] },
    { n: 'Service "Print Spooler" is not running',          s: '1', k: ['srv'],                      w: 3,   d: [300, 7200] },
    { n: 'Fibre Channel port 0/{p}: Link down',             s: '4', k: ['stor'],                     w: 2,   d: [300, 10800] }
  ];

  // Same rule as mesai_for() in app.py; the hours are a setting (state.hours).
  function mesaiOf(clock) {
    const d = new Date(clock * 1000), h = state.hours;
    return (h.days.includes((d.getDay() + 6) % 7) && d.getHours() >= h.start && d.getHours() < h.end) ? 'Mesai İçi' : 'Mesai Dışı';
  }

  // ── Sample alarms: last 60 days ──────────────────────────
  const EVENTS = [];
  for (let i = 0; i < 1500; i++) {
    const host = weighted(rnd, HOSTS, h => h.weight);
    const prob = weighted(rnd, PROBLEMS.filter(p => p.k.includes(host.kind)), p => p.w);
    const clock = NOW - Math.floor(rnd() * 60 * DAY);
    const duration = Math.round(prob.d[0] + Math.pow(rnd(), 2) * (prob.d[1] - prob.d[0]));
    const stuck = clock > NOW - 7 * DAY && rnd() < 0.07;      // still open
    const resolved = !stuck && clock + duration <= NOW;
    EVENTS.push({
      clock, r_clock: resolved ? clock + duration : 0,
      severity: prob.s, name: prob.n.replace('{p}', rint(rnd, 1, 24)),
      host: host.name, groupId: host.groupId,
      resolved, duration: resolved ? duration : 0
    });
  }
  EVENTS.sort((a, b) => a.clock - b.clock);
  EVENTS.forEach((e, i) => { e.eventid = String(4810000 + i * 3); });

  function eventsBetween(from, till, groupIds) {
    const groups = groupIds && groupIds.length ? new Set(groupIds.map(String)) : null;
    return EVENTS.filter(e => e.clock >= from && e.clock <= till && (!groups || groups.has(e.groupId)));
  }
  // Public shape of an alarm-report row (drops the internal groupId).
  const reportRow = e => ({
    eventid: e.eventid, clock: e.clock, r_clock: e.r_clock, severity: e.severity,
    name: e.name, host: e.host, resolved: e.resolved, duration: e.duration, mesai: mesaiOf(e.clock)
  });

  // ── Mutable state (kept for the browser tab) ─────────────
  const isoDate = ts => { const d = new Date(ts * 1000); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };
  const DEFAULT_STATE = {
    activeId: 1, nextId: 4,
    servers: [
      { id: 1, name: 'Demo Zabbix', baseUrl: 'https://zabbix.example.com',    username: 'api-report', verifyTls: true },
      { id: 2, name: 'DR Zabbix',   baseUrl: 'https://zabbix-dr.example.com', username: 'api-report', verifyTls: true },
      { id: 3, name: 'Lab Zabbix',  baseUrl: 'http://10.0.0.5',               username: 'Admin',      verifyTls: true }
    ],
    hours: { start: 8, end: 18, days: [0, 1, 2, 3, 4] },
    nextScheduleId: 3,
    schedules: [
      { id: 1, name: 'Weekly infrastructure report', serverId: 1, frequency: 'weekly', hour: 8, weekday: 0, monthday: 1,
        groupIds: [], groupNames: [], severities: ['2', '3', '4', '5'],
        recipients: 'noc@example.com, it-managers@example.com', lang: 'en', enabled: true },
      { id: 2, name: 'Monthly network report', serverId: 1, frequency: 'monthly', hour: 7, weekday: 0, monthday: 1,
        groupIds: ['14', '15'], groupNames: ['Network / Switches', 'Network / Firewalls'], severities: ['3', '4', '5'],
        recipients: 'network-team@example.com', lang: 'en', enabled: true }
    ],
    smtp: { host: 'smtp.example.com', port: 587, security: 'starttls', username: 'zabbix-reports', fromAddr: 'zabbix-reports@example.com', hasPassword: true },
    nobet: {
      cycleStart: isoDate(NOW - 30 * DAY), dayStartHour: 8, nightStartHour: 20,
      cycle: [
        { gunduz: 'Ahmet Yılmaz',  gece: 'Mehmet Kaya' },
        { gunduz: 'Elif Demir',    gece: 'Selin Çelik' },
        { gunduz: 'Mehmet Kaya',   gece: 'Ahmet Yılmaz' },
        { gunduz: 'Zeynep Arslan', gece: 'Elif Demir' }
      ]
    }
  };
  const STATE_KEY = 'zrsDemoState';
  let state;
  try { state = JSON.parse(sessionStorage.getItem(STATE_KEY)); } catch (e) { state = null; }
  if (!state || !state.servers || !state.nobet || !state.schedules || !state.hours) state = JSON.parse(JSON.stringify(DEFAULT_STATE));
  const saveState = () => { try { sessionStorage.setItem(STATE_KEY, JSON.stringify(state)); } catch (e) {} };
  let cacheCleared = false;

  // Mirrors nobetci_for() in app.py.
  function nobetciFor(clock) {
    const cfg = state.nobet;
    if (!cfg.cycleStart || !cfg.cycle.length) return '';
    const dt = new Date(clock * 1000), h = dt.getHours();
    const start = new Date(cfg.cycleStart + 'T00:00:00');
    let vardiya, gun;
    if (h >= cfg.dayStartHour && h < cfg.nightStartHour) { vardiya = 'gunduz'; gun = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()); }
    else { vardiya = 'gece'; gun = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate() - (h < cfg.dayStartHour ? 1 : 0)); }
    const n = cfg.cycle.length, delta = Math.round((gun - start) / 86400000);
    return cfg.cycle[((delta % n) + n) % n][vardiya] || '';
  }

  // ── ACK report rows ──────────────────────────────────────
  const OPERATORS = ['a.yilmaz', 'm.kaya', 'e.demir', 's.celik', 'z.arslan'];
  const ACK_NOTES = ['Investigating', 'Forwarded to the owning team', 'Part of planned work', 'Service restarted',
                     'Disk cleaned up', 'Transient flap, monitoring', 'Vendor case opened', ''];
  function ackRow(e) {
    const r = mulberry32(Number(e.eventid));
    const acks = [];
    if (r() < (e.resolved ? 0.8 : 0.6)) {
      const x = r();
      const delay = x < 0.6 ? rint(r, 60, 1700) : x < 0.82 ? rint(r, 1800, 3500) : rint(r, 3700, 14000);
      if (e.clock + delay <= NOW) {
        acks.push({ clock: e.clock + delay, username: OPERATORS[rint(r, 0, OPERATORS.length - 1)], message: ACK_NOTES[rint(r, 0, ACK_NOTES.length - 1)] });
        const later = e.clock + delay + rint(r, 600, 3600);
        if (r() < 0.2 && later <= NOW) acks.push({ clock: later, username: OPERATORS[rint(r, 0, OPERATORS.length - 1)], message: 'Issue resolved' });
      }
    }
    const first = acks[0];
    return {
      eventid: e.eventid, clock: e.clock, r_clock: e.r_clock,
      clear_sec: e.resolved ? e.duration : -1, name: e.name, severity: e.severity, host: e.host,
      ack_status: first ? 'ack' : 'noack', ack_clock: first ? first.clock : 0,
      ack_delay_sec: first ? first.clock - e.clock : -1, ack_username: first ? first.username : '',
      ack_message: first ? first.message : '', ack_count: acks.length, all_acks: acks,
      nobetci: nobetciFor(e.clock)
    };
  }

  // ── Other canned data ────────────────────────────────────
  function dashboardStats() {
    const recent = EVENTS.filter(e => e.clock >= NOW - 30 * DAY);
    const sevDist = {}, activeBySev = {}, perHost = {}, durations = [];
    let active = 0;
    recent.forEach(e => {
      sevDist[e.severity] = (sevDist[e.severity] || 0) + 1;
      perHost[e.host] = (perHost[e.host] || 0) + 1;
      if (e.resolved) durations.push(e.duration);
    });
    EVENTS.forEach(e => { if (!e.resolved) { active++; activeBySev[e.severity] = (activeBySev[e.severity] || 0) + 1; } });
    durations.sort((a, b) => a - b);
    const n = durations.length;
    return {
      activeProblems: active, activeBySeverity: activeBySev,
      total30: recent.length, severityDist: sevDist,
      topHosts: Object.entries(perHost).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([name, count]) => ({ name, count })),
      monitoredHosts: HOSTS.filter(h => h.enabled).length,
      avgResolveSec: n ? Math.round(durations.reduce((a, b) => a + b, 0) / n) : 0,
      medianResolveSec: n ? durations[Math.floor(n / 2)] : 0,
      resolvedSampleCount: n
    };
  }

  const ALL_CAT = 'All Devices';
  function inventoryRows() {
    return HOSTS.filter(h => h.serial).map(h => ({
      lokasyon: h.group, serial: h.serial, name: h.name, hostname: h.hostname,
      ip: h.ip, status: h.enabled ? 'Enabled' : 'Disabled'
    })).sort((a, b) => (a.lokasyon + a.name).toLowerCase().localeCompare((b.lokasyon + b.name).toLowerCase()));
  }

  const tkoduRnd = mulberry32(77);
  const TKODU = Array.from({ length: 40 }, (_, i) => {
    const host = HOSTS[rint(tkoduRnd, 0, HOSTS.length - 1)];
    const prob = weighted(tkoduRnd, PROBLEMS.filter(p => p.k.includes(host.kind)), p => p.w);
    return {
      triggerid: String(23100 + i * 11),
      description: prob.n.replace('{p}', rint(tkoduRnd, 1, 24)),
      status: tkoduRnd() < 0.83 ? 'Enabled' : 'Disabled',
      priority: prob.s,
      hosts: [{ name: host.name, status: host.enabled ? 'Enabled' : 'Disabled' }]
    };
  });

  const SLA = [
    { slaName: 'Critical Services', serviceName: 'Portal',          slo: 99.9, sli: 99.9712 },
    { slaName: 'Critical Services', serviceName: 'API Gateway',     slo: 99.9, sli: 99.8140 },
    { slaName: 'Critical Services', serviceName: 'Database',        slo: 99.9, sli: 99.9455 },
    { slaName: 'Infrastructure',   serviceName: 'Core Network',    slo: 99.5, sli: 99.9903 },
    { slaName: 'Infrastructure',   serviceName: 'Virtualization',   slo: 99.5, sli: 99.3020 },
    { slaName: 'Infrastructure',   serviceName: 'Storage',         slo: 99.5, sli: 100 }
  ].map(s => Object.assign(s, { status: s.sli >= s.slo ? 'ok' : 'violation' }));

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src; s.onload = resolve; s.onerror = () => reject(new Error('could not load script'));
      document.head.appendChild(s);
    });
  }
  async function inventoryWorkbook() {
    if (!window.XLSX) await loadScript('https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js');
    const tr = I18N.lang === 'tr';
    const head = tr ? ['Lokasyon', 'Seri Numarası', 'Ad', 'Hostname', 'IP Adresi', 'Durum']
                    : ['Location', 'Serial Number', 'Name', 'Hostname', 'IP Address', 'Status'];
    const status = v => (tr ? (v === 'Enabled' ? 'Aktif' : 'Pasif') : v);
    const rows = inventoryRows().map(r => ({
      [head[0]]: r.lokasyon, [head[1]]: r.serial, [head[2]]: r.name,
      [head[3]]: r.hostname, [head[4]]: r.ip, [head[5]]: status(r.status)
    }));
    const ws = XLSX.utils.json_to_sheet(rows);
    ws['!cols'] = [40, 30, 38, 38, 18, 12].map(wch => ({ wch }));
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, tr ? 'Tüm Cihazlar' : ALL_CAT);
    return new Blob([XLSX.write(wb, { type: 'array', bookType: 'xlsx' })],
      { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  }

  // ── Scheduled reports (mirrors reporting.next_run / period_for) ──
  function nextRun(s) {
    const now = new Date();
    const at = (y, mo, d) => new Date(y, mo, d, s.hour, 0, 0);
    let c;
    if (s.frequency === 'weekly') {
      c = at(now.getFullYear(), now.getMonth(), now.getDate() + ((s.weekday - (now.getDay() + 6) % 7) + 7) % 7);
      if (c <= now) c = at(c.getFullYear(), c.getMonth(), c.getDate() + 7);
    } else if (s.frequency === 'monthly') {
      const day = (y, mo) => Math.min(s.monthday, new Date(y, mo + 1, 0).getDate());
      c = at(now.getFullYear(), now.getMonth(), day(now.getFullYear(), now.getMonth()));
      if (c <= now) c = at(now.getFullYear(), now.getMonth() + 1, day(now.getFullYear(), now.getMonth() + 1));
    } else {
      c = at(now.getFullYear(), now.getMonth(), now.getDate());
      if (c <= now) c = at(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    }
    return Math.floor(c / 1000);
  }
  function periodFor(frequency) {
    const now = new Date();
    let end = new Date(now.getFullYear(), now.getMonth(), now.getDate()), start;
    if (frequency === 'monthly') { end = new Date(now.getFullYear(), now.getMonth(), 1); start = new Date(now.getFullYear(), now.getMonth() - 1, 1); }
    else start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - (frequency === 'weekly' ? 7 : 1));
    return [Math.floor(start / 1000), Math.floor(end / 1000) - 1];
  }
  function scheduleRow(s) {
    const server = state.servers.find(x => x.id === s.serverId);
    return Object.assign({ lastRun: 0, lastStatus: '' }, s,
      { serverName: server ? server.name : '', nextRun: nextRun(s) });
  }
  function scheduleError(b) {
    if (!b.name) return 'A name is required.';
    const mails = (b.recipients || '').split(/[\s,;]+/).filter(Boolean);
    if (!mails.length) return 'At least one recipient is required.';
    const bad = mails.find(m => !/^[^@]+@[^@]+\.[^@]+$/.test(m));
    if (bad) return 'Invalid e-mail address: ' + bad;
    if (!(b.severities || []).length) return 'Select at least one severity';
    return null;
  }
  const scheduleFields = b => ({
    name: b.name, serverId: Number(b.serverId), frequency: b.frequency, hour: Number(b.hour),
    weekday: Number(b.weekday), monthday: Number(b.monthday),
    groupIds: (b.groups || []).map(g => String(g.id)), groupNames: (b.groups || []).map(g => g.name),
    severities: b.severities, recipients: (b.recipients || '').split(/[\s,;]+/).filter(Boolean).join(', '),
    lang: b.lang || 'en', enabled: b.enabled !== false
  });

  // ── Background jobs (report / ACK), finished after a few polls ──
  const jobs = {};
  let jobSeq = 0;
  function startJob(result, steps) {
    const id = 'demo-job-' + (++jobSeq);
    jobs[id] = { result, steps, polls: 0, cancelled: false };
    return id;
  }
  function jobProgress(id) {
    const job = jobs[id];
    if (!job) return { success: true, data: { percent: 0, step: 'Starting...', events: 0 }, status: 'unknown' };
    if (job.cancelled) return { success: true, data: { percent: 0, step: 'Cancelled', events: 0 }, status: 'cancelled' };
    const i = Math.min(job.polls++, job.steps.length - 1);
    const done = i === job.steps.length - 1;
    return {
      success: true, error: null, status: done ? 'done' : 'running',
      data: { percent: job.steps[i][0], step: job.steps[i][1], events: Math.round(job.result.length * job.steps[i][0] / 100) }
    };
  }
  const REPORT_STEPS = n => [[25, 'Fetching active problems...'], [70, 'Fetching trigger details...'], [100, 'Report ready! (' + n + ' alarms)']];

  // ── Routes ───────────────────────────────────────────────
  const ok = data => ({ success: true, data });
  const serverById = id => state.servers.find(s => s.id === Number(id));
  const DEMO_SESSION = 'demo-session';

  const ROUTES = [
    ['GET', /^\/api\/servers$/, () => ({
      success: true, activeId: state.activeId, canManage: true,
      servers: state.servers.map(s => Object.assign({ isActive: s.id === state.activeId }, s))
    })],
    ['POST', /^\/api\/servers$/, b => {
      if (!b.name || !b.url || !b.username) return [400, { success: false, error: 'Name, URL and username are required.' }];
      const s = { id: state.nextId++, name: b.name, baseUrl: b.url.replace(/\/(api_jsonrpc\.php)?$/i, ''), username: b.username, verifyTls: b.verifyTls !== false };
      state.servers.push(s); saveState();
      return { success: true, id: s.id };
    }],
    ['POST', /^\/api\/servers\/check$/, () => {
      const results = {};
      state.servers.forEach(s => {
        results[s.id] = /example\.com/.test(s.baseUrl) ? { ok: true } : { ok: false, error: 'Connection timed out' };
      });
      return { success: true, results };
    }],
    ['POST', /^\/api\/servers\/(\d+)\/activate$/, (b, m) => {
      if (!serverById(m[1])) return [404, { success: false, error: 'Server not found.' }];
      state.activeId = Number(m[1]); saveState();
      return { success: true };
    }],
    ['POST', /^\/api\/servers\/(\d+)\/delete$/, (b, m) => {
      state.servers = state.servers.filter(s => s.id !== Number(m[1]));
      state.schedules = state.schedules.filter(s => s.serverId !== Number(m[1]));
      if (state.activeId === Number(m[1])) state.activeId = null;
      saveState();
      return { success: true };
    }],
    ['POST', /^\/api\/servers\/(\d+)$/, (b, m) => {
      const s = serverById(m[1]);
      if (!s) return [404, { success: false, error: 'Server not found.' }];
      if (!b.name || !b.url || !b.username) return [400, { success: false, error: 'Name, URL and username are required.' }];
      Object.assign(s, { name: b.name, baseUrl: b.url.replace(/\/(api_jsonrpc\.php)?$/i, ''), username: b.username, verifyTls: b.verifyTls !== false });
      saveState();
      return { success: true };
    }],

    ['GET', /^\/api\/schedules$/, () => ({
      success: true, isAdmin: true, smtpConfigured: !!state.smtp.host, activeServerId: state.activeId,
      servers: state.servers.map(s => ({ id: s.id, name: s.name })),
      schedules: state.schedules.map(scheduleRow)
    })],
    ['POST', /^\/api\/schedules$/, b => {
      const err = scheduleError(b);
      if (err) return [400, { success: false, error: err }];
      state.schedules.push(Object.assign({ id: state.nextScheduleId++ }, scheduleFields(b))); saveState();
      return { success: true };
    }],
    ['POST', /^\/api\/schedules\/hostgroups$/, () => ok(GROUPS.map(g => ({ id: g.id, name: g.name })))],
    ['POST', /^\/api\/schedules\/(\d+)\/delete$/, (b, m) => {
      state.schedules = state.schedules.filter(s => s.id !== Number(m[1])); saveState();
      return { success: true };
    }],
    ['POST', /^\/api\/schedules\/(\d+)\/run$/, (b, m) => {
      const s = state.schedules.find(x => x.id === Number(m[1]));
      if (!s) return [404, { success: false, error: 'Schedule not found.' }];
      const p = periodFor(s.frequency);
      const n = eventsBetween(p[0], p[1], s.groupIds).filter(e => s.severities.includes(e.severity)).length;
      s.lastRun = Math.floor(Date.now() / 1000);
      s.lastStatus = 'Sent to ' + s.recipients.split(',').length + ' recipients — ' + n + ' alarms';
      saveState();
      return { success: true };
    }],
    ['POST', /^\/api\/schedules\/(\d+)$/, (b, m) => {
      const s = state.schedules.find(x => x.id === Number(m[1]));
      if (!s) return [404, { success: false, error: 'Schedule not found.' }];
      const err = scheduleError(b);
      if (err) return [400, { success: false, error: err }];
      Object.assign(s, scheduleFields(b)); saveState();
      return { success: true };
    }],
    ['GET', /^\/api\/smtp$/, () => ({ success: true, config: state.smtp })],
    ['POST', /^\/api\/smtp$/, b => {
      if (!b.host || !b.port) return [400, { success: false, error: 'SMTP host and a valid port are required.' }];
      state.smtp = { host: b.host, port: Number(b.port), security: b.security, username: b.username || '', fromAddr: b.fromAddr || '',
                     hasPassword: !!b.password || state.smtp.hasPassword };
      saveState();
      return { success: true };
    }],
    ['POST', /^\/api\/smtp\/test$/, () => ({ success: true })],

    ['GET', /^\/api\/dashboard\/stats$/, () => ({
      success: true, data: dashboardStats(), server: 'Demo Zabbix',
      cachedAt: NOW, age: 0, ttl: 300, fromCache: false
    })],

    ['GET', /^\/api\/business-hours$/, () => ({ success: true, canEdit: true, hours: state.hours })],
    ['POST', /^\/api\/business-hours$/, b => {
      state.hours = { start: Number(b.start), end: Number(b.end), days: (b.days || []).map(Number) }; saveState();
      return { success: true };
    }],
    ['GET', /^\/api\/nobet$/, () => ({ success: true, canEdit: true, config: state.nobet })],
    ['POST', /^\/api\/nobet$/, b => {
      state.nobet = {
        cycleStart: b.cycleStart || '', dayStartHour: Number(b.dayStartHour), nightStartHour: Number(b.nightStartHour),
        cycle: (b.cycle || []).map(d => ({ gunduz: d.gunduz || '', gece: d.gece || '' }))
      };
      saveState();
      return { success: true };
    }],

    ['POST', /^\/api\/ack\/hostgroups$/, () => ok(GROUPS.map(g => ({ groupid: g.id, name: g.name, host_count: g.hosts.length })))],
    ['POST', /^\/api\/ack$/, b => {
      const rows = (b.mode === 'history'
        ? eventsBetween(Number(b.timeFrom), Number(b.timeTill), b.groupIds)
        : eventsBetween(0, NOW, b.groupIds).filter(e => !e.resolved)
      ).map(ackRow).reverse();
      return { success: true, jobId: startJob(rows, REPORT_STEPS(rows.length)) };
    }],
    ['POST', /^\/api\/ack\/progress$/, b => jobProgress(b.jobId)],
    ['POST', /^\/api\/ack\/result$/, b => jobs[b.jobId]
      ? { success: true, data: jobs[b.jobId].result, total: jobs[b.jobId].result.length }
      : [404, { success: false, error: 'Job not found' }]],
    ['POST', /^\/api\/ack\/cancel$/, b => { if (jobs[b.jobId]) jobs[b.jobId].cancelled = true; return { success: true }; }],

    ['POST', /^\/api\/generate$/, () => {
      const rows = inventoryRows();
      const enabled = rows.filter(r => r.status === 'Enabled').length;
      const summary = { TOTAL: rows.length };
      summary[ALL_CAT] = { total: rows.length, serial_found: rows.length, enabled, disabled: rows.length - enabled };
      const d = new Date(), p = n => String(n).padStart(2, '0');
      return {
        success: true, data: { [ALL_CAT]: rows }, eksik: {}, summary, mode: 'all', categories: [ALL_CAT],
        generated_at: isoDate(d / 1000) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds())
      };
    }],
    ['POST', /^\/api\/download$/, async () => new Response(await inventoryWorkbook(), { status: 200 })],

    ['POST', /^\/api\/tkodu$/, b => {
      const code = (b.code || '').trim().toLowerCase();
      if (!code) return [400, { success: false, error: 'The search text cannot be empty.' }];
      const data = TKODU.filter(t => t.description.toLowerCase().includes(code));
      return { success: true, data, total: data.length };
    }],

    ['POST', /^\/alarm\/api\/auto-login$/, () => ({ success: true, sessionId: DEMO_SESSION, url: 'https://zabbix.example.com' })],
    ['POST', /^\/alarm\/api\/logout$/, () => ({ success: true })],
    ['POST', /^\/alarm\/api\/hostgroups$/, () => ok(GROUPS.map(g => ({ groupid: g.id, name: g.name, hosts: g.hosts.length })))],
    ['POST', /^\/alarm\/api\/report-data$/, b => {
      const rows = eventsBetween(Number(b.timeFrom), Number(b.timeTill), b.groupIds).map(reportRow);
      return { success: true, jobId: startJob(rows, REPORT_STEPS(rows.length)), message: 'Report generation started' };
    }],
    ['POST', /^\/alarm\/api\/report-progress$/, b => jobProgress(b.jobId)],
    ['POST', /^\/alarm\/api\/report-result$/, b => jobs[b.jobId] ? ok(jobs[b.jobId].result) : [404, { success: false, error: 'Job not found' }]],
    ['POST', /^\/alarm\/api\/report-cancel$/, b => { if (jobs[b.jobId]) jobs[b.jobId].cancelled = true; return { success: true }; }],
    ['POST', /^\/alarm\/api\/sla-data$/, () => ok(SLA)],
    ['POST', /^\/alarm\/api\/cache-stats$/, () => ok(cacheCleared
      ? { events: 0, coverage_days: 0, db_size_mb: '0.0 MB (SQLite)' }
      : { events: EVENTS.length, coverage_days: 60, db_size_mb: '1.8 MB (SQLite)' })],
    ['POST', /^\/alarm\/api\/cache-clear$/, () => { cacheCleared = true; return { success: true }; }],
    ['POST', /^\/alarm\/api\/cache-browse$/, b => {
      const from = Number(b.timeFrom) || 0, till = Number(b.timeTill) || NOW;
      const q = (b.search || '').trim().toLowerCase(), page = Math.max(1, Number(b.page) || 1), perPage = 50;
      let rows = cacheCleared ? [] : eventsBetween(from, till);
      if (q) rows = rows.filter(e => e.host.toLowerCase().includes(q) || e.name.toLowerCase().includes(q));
      rows = rows.slice().reverse();
      const coverage = [];
      if (!cacheCleared) {
        for (let d = Math.floor(Math.max(from, NOW - 60 * DAY) / DAY) * DAY; d <= Math.min(till, NOW); d += DAY) coverage.push({ day: d, fetched_at: NOW });
      }
      return {
        success: true, total: rows.length, page, per_page: perPage,
        total_pages: Math.ceil(rows.length / perPage),
        data: rows.slice((page - 1) * perPage, page * perPage).map(reportRow),
        coverage
      };
    }]
  ];

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  window.fetch = async function (input, init) {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const method = ((init && init.method) || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
    const route = url.origin === location.origin &&
      ROUTES.find(r => r[0] === method && r[1].test(url.pathname));
    if (!route) return realFetch(input, init);

    let body = {};
    try { if (init && typeof init.body === 'string' && init.body) body = JSON.parse(init.body) || {}; } catch (e) {}
    await sleep(150 + Math.random() * 250);   // feel like a network round trip
    let out = await route[2](body, url.pathname.match(route[1]));
    if (out instanceof Response) return out;
    let status = 200;
    if (Array.isArray(out)) { status = out[0]; out = out[1]; }
    return new Response(JSON.stringify(out), { status, headers: { 'Content-Type': 'application/json' } });
  };

  // ── Page chrome: demo badge, forms, auto-loading ─────────
  function notice(msg) {
    let el = document.getElementById('zrsDemoNotice');
    if (!el) {
      el = document.createElement('div');
      el.id = 'zrsDemoNotice';
      el.style.cssText = 'position:fixed;left:50%;bottom:58px;transform:translateX(-50%);z-index:2147483646;background:#1b1810;color:#f3efe6;border:1px solid rgba(245,158,11,.45);border-radius:10px;padding:10px 16px;font:500 13px "IBM Plex Sans",system-ui,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.45);transition:opacity .2s';
      document.body.appendChild(el);
    }
    el.textContent = msg; el.style.opacity = '1';
    clearTimeout(notice.timer);
    notice.timer = setTimeout(() => { el.style.opacity = '0'; }, 2800);
  }

  // There is no server behind the demo: sign-in always succeeds, user edits are not stored.
  document.addEventListener('submit', e => {
    e.preventDefault(); e.stopPropagation();
    if (e.target.querySelector('input[type=password]') && !e.target.querySelector('select')) location.href = 'index.html';
    else notice(I18N.t('User changes are not saved in the demo.'));
  }, true);

  function whenReady(test, run) {
    let tries = 0;
    const t = setInterval(() => {
      let ready = false;
      try { ready = test(); } catch (e) {}
      if (ready) { clearInterval(t); run(); }
      else if (++tries > 100) clearInterval(t);
    }, 100);
  }

  window.addEventListener('DOMContentLoaded', () => {
    const badge = document.createElement('div');
    badge.style.cssText = 'position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:2147483646;display:flex;align-items:center;gap:10px;background:#14110b;color:#c7c0b0;border:1px solid rgba(240,230,200,.18);border-radius:999px;padding:7px 16px 7px 8px;font:500 12px "IBM Plex Sans",system-ui,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.4);white-space:nowrap';
    badge.innerHTML = '<span style="background:#f59e0b;color:#2a1e05;font-weight:700;border-radius:999px;padding:2px 9px;letter-spacing:.06em">DEMO</span>'
      + '<span>Sample data — not connected to a real Zabbix</span>';
    document.body.appendChild(badge);

    const login = document.querySelector('form input[name=username]');
    if (login && document.querySelector('form input[name=password]') && !document.querySelector('form select')) {
      login.value = 'demo';
      document.querySelector('form input[name=password]').value = 'demo';
    }

    // Pages that wait for a click in the real app: show data straight away.
    if (document.getElementById('generateReportBtn')) {
      whenReady(() => reportState.sessionId && reportState.hostGroups.length,
        () => document.getElementById('generateReportBtn').click());
    }
    if (typeof window.fetchTkodu === 'function') {
      document.getElementById('codeInput').value = 'link';
      window.fetchTkodu();
    }
    if (document.getElementById('coverageGrid') && typeof window.loadData === 'function') window.loadData(1);
  });
})();

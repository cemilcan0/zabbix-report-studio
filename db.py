"""
Alarm report cache - SQLite (Windows compatible)
"""
import os
import hashlib
import time as time_module
import sqlite3
from datetime import datetime, timedelta

# The database file lives in the writable data directory
# (when frozen: %LOCALAPPDATA%\Zabbix Report Studio)
import paths
BASE_DIR = paths.DATA_DIR
DB_PATH = paths.data_path("cache.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS enriched_events (
            eventid TEXT PRIMARY KEY,
            clock INTEGER NOT NULL,
            r_clock INTEGER DEFAULT 0,
            severity TEXT DEFAULT '0',
            name TEXT DEFAULT '',
            host TEXT DEFAULT '',
            resolved INTEGER DEFAULT 0,
            duration INTEGER DEFAULT 0,
            mesai TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS event_cache_map (
            eventid TEXT NOT NULL,
            group_set_key TEXT NOT NULL,
            PRIMARY KEY (eventid, group_set_key)
        );
        CREATE TABLE IF NOT EXISTS cache_coverage (
            group_set_key TEXT NOT NULL,
            day_start INTEGER NOT NULL,
            fetched_at REAL NOT NULL,
            PRIMARY KEY (group_set_key, day_start)
        );
        CREATE INDEX IF NOT EXISTS idx_events_clock ON enriched_events(clock);
        CREATE INDEX IF NOT EXISTS idx_cache_map_key ON event_cache_map(group_set_key);
    ''')
    conn.commit()
    cur.close()
    conn.close()


def compute_group_set_key(group_ids, scope=''):
    """
    Cache key for a set of groups. If `scope` (the active Zabbix server id) is
    given, the key becomes server-specific, so different Zabbix servers do not
    collide in the same cache file.
    """
    base = '__ALL__' if not group_ids else hashlib.md5(
        ','.join(sorted(str(g) for g in group_ids)).encode()).hexdigest()
    return f'{scope}:{base}' if scope else base


def get_day_boundaries(time_from, time_till):
    start_dt = datetime.fromtimestamp(int(time_from)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = datetime.fromtimestamp(int(time_till))
    days, current = [], start_dt
    while current < end_dt:
        days.append((int(current.timestamp()), int((current + timedelta(days=1)).timestamp())))
        current += timedelta(days=1)
    return days


def is_today(day_start):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start >= int(today.timestamp())


def get_uncovered_days(group_set_key, time_from, time_till):
    conn = get_db()
    cur = conn.cursor()
    days = get_day_boundaries(time_from, time_till)
    if not days:
        cur.close()
        conn.close()
        return []
    placeholders = ','.join('?' * len(days))
    cur.execute(
        f'SELECT day_start FROM cache_coverage WHERE group_set_key = ? AND day_start IN ({placeholders})',
        [group_set_key] + [d[0] for d in days]
    )
    covered = set(r[0] for r in cur.fetchall())
    cur.close()
    conn.close()
    return [(ds, de) for ds, de in days if ds not in covered or is_today(ds)]


def merge_day_ranges(day_ranges):
    if not day_ranges:
        return []
    merged = []
    cs, ce = day_ranges[0]
    for s, e in day_ranges[1:]:
        if s == ce:
            ce = e
        else:
            merged.append((cs, ce))
            cs, ce = s, e
    merged.append((cs, ce))
    return merged


def _scoped_eid(eventid, scope):
    """Namespaces the enriched_events PK per server (so the same eventid on
    different Zabbix servers does not collide)."""
    return f'{scope}#{eventid}' if scope else str(eventid)


def _unscope_eid(eventid, scope):
    prefix = f'{scope}#'
    return eventid[len(prefix):] if scope and eventid.startswith(prefix) else eventid


def get_cached_events(group_set_key, time_from, time_till, scope=''):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT DISTINCT e.* FROM enriched_events e
        JOIN event_cache_map m ON e.eventid = m.eventid
        WHERE m.group_set_key = ? AND e.clock >= ? AND e.clock <= ?
        ORDER BY e.clock ASC
    ''', (group_set_key, int(time_from), int(time_till)))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    for r in rows:
        r['resolved'] = bool(r['resolved'])
        r['eventid'] = _unscope_eid(r['eventid'], scope)
    return rows


def save_events_to_cache(events, group_set_key, uncovered_days, scope=''):
    if not events and not uncovered_days:
        return
    conn = get_db()
    cur = conn.cursor()
    if events:
        cur.executemany('''
            INSERT OR REPLACE INTO enriched_events
                (eventid, clock, r_clock, severity, name, host, resolved, duration, mesai)
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', [(_scoped_eid(e['eventid'], scope), e['clock'], e.get('r_clock', 0),
               str(e.get('severity', '0')), e.get('name', ''), e.get('host', ''),
               1 if e.get('resolved') else 0, e.get('duration', 0), e.get('mesai', ''))
              for e in events])
        cur.executemany('''
            INSERT OR IGNORE INTO event_cache_map (eventid, group_set_key)
            VALUES (?,?)
        ''', [(_scoped_eid(e['eventid'], scope), group_set_key) for e in events])

    now = time_module.time()
    coverage = [(group_set_key, ds, now) for ds, _ in uncovered_days if not is_today(ds)]
    if coverage:
        cur.executemany('''
            INSERT OR REPLACE INTO cache_coverage (group_set_key, day_start, fetched_at)
            VALUES (?,?,?)
        ''', coverage)
    conn.commit()
    cur.close()
    conn.close()


def clear_cache(scope=''):
    """Delete cached events — of one server when `scope` (its id) is given,
    otherwise everything."""
    conn = get_db()
    cur = conn.cursor()
    if scope:
        cur.execute('DELETE FROM event_cache_map WHERE group_set_key LIKE ?', (f'{scope}:%',))
        cur.execute('DELETE FROM enriched_events WHERE eventid LIKE ?', (f'{scope}#%',))
        cur.execute('DELETE FROM cache_coverage WHERE group_set_key LIKE ?', (f'{scope}:%',))
    else:
        cur.execute('DELETE FROM event_cache_map')
        cur.execute('DELETE FROM enriched_events')
        cur.execute('DELETE FROM cache_coverage')
    conn.commit()
    cur.close()
    conn.close()


def get_cache_stats(scope=''):
    """Event and day counts — of one server when `scope` is given. The file size
    is always that of the whole cache."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM enriched_events WHERE eventid LIKE ?', (f'{scope}#%' if scope else '%',))
    events = cur.fetchone()[0]
    cur.execute('SELECT COUNT(DISTINCT day_start) FROM cache_coverage WHERE group_set_key LIKE ?',
                (f'{scope}:%' if scope else '%',))
    days = cur.fetchone()[0]
    db_size = os.path.getsize(DB_PATH) / (1024 * 1024) if os.path.exists(DB_PATH) else 0
    cur.close()
    conn.close()
    return {'events': events, 'coverage_days': days, 'db_size_mb': f'{db_size:.1f} MB (SQLite)'}

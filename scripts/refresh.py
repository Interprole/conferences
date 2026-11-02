"""Resumable, rate-limited collection of public LINGUIST List announcements."""
import argparse
import json
import re
import signal
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, parse_qs
from urllib.request import Request, urlopen
from catalog import Page, parse_issue, merge, key

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = 'https://linguistlist.org/conf/search/'
RECENT = 'https://linguistlist.org/issues/?topic=Calls&topic=Conferences'
FEEDS = ['https://linguistlist.org/issues/rss/calls', 'https://linguistlist.org/issues/rss/confs']
QUEUE = 'data/linguistlist-crawl.json'
ISSUE = re.compile(r'https://linguistlist\.org/issues/\d+/\d+/$')


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def load(path, default):
    try:
        return json.loads((ROOT / path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def save(path, data):
    dest = ROOT / path
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(dest)


def canonical(url):
    """Only fetch public listing pages, feeds and numbered posts."""
    p = urlsplit(url)
    if p.scheme != 'https' or p.netloc != 'linguistlist.org':
        return None
    if re.fullmatch(r'/issues/\d+/\d+/?', p.path):
        return 'https://linguistlist.org' + p.path.rstrip('/') + '/'
    page = parse_qs(p.query).get('page', ['1'])[0]
    if not page.isdigit() or not 1 <= int(page) <= 10000:
        return None
    if p.path.rstrip('/') == '/conf/search':
        return CATALOGUE + ('?page=' + str(int(page)) if int(page) > 1 else '')
    if p.path == '/issues/':
        return RECENT + ('&page=' + str(int(page)) if int(page) > 1 else '')
    if url in FEEDS:
        return url
    return None


def retry_delay(headers, attempt):
    raw = headers.get('Retry-After') if headers else None
    if raw:
        try:
            return max(0, float(raw))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
    return 10 * 2 ** attempt


class FetchFailure(Exception):
    def __init__(self, message, blocked=False, cooldown=0):
        super().__init__(message)
        self.blocked, self.cooldown = blocked, cooldown


class Client:
    def __init__(self, interval=2, retries=3, timeout=45, stop=None):
        self.interval, self.retries, self.timeout = interval, retries, timeout
        self.stop = stop or (lambda: False)
        self.last_request = None

    def pause(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stop():
                raise FetchFailure('Run time limit reached')
            time.sleep(max(0, min(1, end - time.monotonic())))

    def fetch(self, url):
        for attempt in range(self.retries):
            if self.stop():
                raise FetchFailure('Run time limit reached')
            if self.last_request is not None:
                self.pause(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                request = Request(url, headers={
                    'User-Agent': 'ConferenceDirectory/2.0 (+https://github.com/Interprole/conferences)',
                    'Accept': 'text/html,application/rss+xml,application/xml;q=0.9'})
                with urlopen(request, timeout=self.timeout) as response:
                    return response.read().decode('utf-8')
            except HTTPError as exc:
                if exc.code == 403:
                    raise FetchFailure('HTTP 403: access denied; pending work retained', blocked=True) from exc
                if exc.code != 429 and not 500 <= exc.code < 600:
                    raise FetchFailure('HTTP ' + str(exc.code)) from exc
                delay = retry_delay(exc.headers, attempt)
                if attempt + 1 == self.retries or delay > 120:
                    raise FetchFailure('HTTP ' + str(exc.code), blocked=exc.code == 429, cooldown=delay) from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt + 1 == self.retries:
                    raise FetchFailure(str(exc)[:180]) from exc
                delay = retry_delay(None, attempt)
            self.pause(delay)
        raise FetchFailure('No request attempts configured')


def enqueue(state, url, kind, refresh=False):
    url = canonical(url)
    if not url:
        return
    jobs = state['jobs']
    if url not in jobs:
        jobs[url] = {'kind': kind, 'status': 'pending', 'failures': 0}
    elif refresh and jobs[url]['status'] in ('success', 'ignored'):
        jobs[url]['status'] = 'pending'


def discover(state, html, url, recent_pages):
    page = Page()
    page.feed(html)
    marker = 'Browse and Search Conference Listings' if url.startswith(CATALOGUE) else 'Browse and Search Issues'
    if marker not in page.text():
        raise ValueError('Index did not contain its expected heading')
    if url.startswith(CATALOGUE):
        state['catalogue_pages_expected'] = max(state.get('catalogue_pages_expected', 1), 1)
    for href in page.links:
        target = canonical(urljoin(url, href))
        if not target:
            continue
        if ISSUE.fullmatch(target):
            enqueue(state, target, 'issue')
        elif target.startswith(CATALOGUE):
            # Expand Last into every page; a failed middle page cannot hide the rest.
            last = int(parse_qs(urlsplit(target).query).get('page', ['1'])[0])
            state['catalogue_pages_expected'] = max(state.get('catalogue_pages_expected', 1), last)
            for n in range(1, last + 1):
                enqueue(state, CATALOGUE + ('?page=' + str(n) if n > 1 else ''), 'catalogue')
        elif target.startswith(RECENT):
            n = int(parse_qs(urlsplit(target).query).get('page', ['1'])[0])
            if n <= recent_pages:
                enqueue(state, target, 'recent')


def discover_feed(state, xml):
    root = ET.fromstring(xml)
    if root.tag != 'rss':
        raise ValueError('Expected RSS document')
    for item in root.findall('./channel/item'):
        if re.search(r'\((?:Jrnl|Book)\)', item.findtext('title') or '', re.I):
            continue
        enqueue(state, item.findtext('link') or '', 'issue')


def apply_record(records, record, today):
    """Prefer matching known post URLs if a title was corrected; preserve IDs."""
    match = next((c for c in records if key(c['name'], c.get('start')) == key(record['name'], record.get('start'))), None)
    if match is None:
        urls = {s['url'] for s in record['sources']}
        candidates = [c for c in records if urls.intersection(s['url'] for s in c.get('sources', []))]
        if len(candidates) == 1:
            match = candidates[0]
    if match is not None:
        result = merge(match, record, today)
        records[records.index(match)] = result
        return result['id']
    if record['classification'] != 'excluded':
        records.append(record)
        return record['id']
    return None


def coverage(state, records):
    kinds = {}
    for kind in ('catalogue', 'recent', 'feed', 'issue'):
        selected = [j for j in state['jobs'].values() if j['kind'] == kind]
        kinds[kind] = {'discovered': len(selected),
                       'succeeded': sum(j['status'] == 'success' for j in selected),
                       'ignored': sum(j['status'] == 'ignored' for j in selected),
                       'pending': sum(j['status'] in ('pending', 'error') for j in selected)}
    return {'cycle': kinds, 'catalogue_pages_expected': state.get('catalogue_pages_expected'),
            'records_total': len(records),
            'records_without_issue': sum(not any(ISSUE.fullmatch(s['url']) for s in c.get('sources', [])) for c in records),
            'records_needing_review': sum(c.get('verification') != 'reviewed' for c in records),
            'official_sites_verified': False}


def run(args, root=None, fetcher=None):
    global ROOT
    if root is not None:
        ROOT = Path(root)
    started, began = utcnow(), time.monotonic()
    today = started[:10]
    records = load('data/linguistlist.json', [])
    overrides = load('data/overrides.json', {})
    previous = load('data/linguistlist-status.json', {})
    state = load(QUEUE, {'version': 1, 'jobs': {}, 'cycle_started_at': started})
    if state.get('version') != 1:
        raise ValueError('Unsupported crawl state version')
    if state.get('cycle_complete_at') and state['cycle_complete_at'][:10] < today:
        state = {'version': 1, 'jobs': {}, 'cycle_started_at': started}
    if state.get('daily_seeded_at') != today:
        enqueue(state, CATALOGUE, 'catalogue', refresh=True)
        for n in range(1, args.recent_pages + 1):
            enqueue(state, RECENT + ('&page=' + str(n) if n > 1 else ''), 'recent', refresh=True)
        for feed in FEEDS:
            enqueue(state, feed, 'feed', refresh=True)
        for c in records:
            for source in c.get('sources', []):
                if (not c.get('end') or c['end'] >= today) and ISSUE.fullmatch(source['url']):
                    enqueue(state, source['url'], 'issue', refresh=True)
                if source['url'].startswith(CATALOGUE):
                    enqueue(state, source['url'], 'catalogue')
        state['daily_seeded_at'] = today
    stopped = [False]
    stop = lambda: stopped[0] or time.monotonic() - began >= args.max_seconds
    client = Client(args.interval, args.retries, args.timeout, stop)
    fetcher = fetcher or client.fetch
    counts = {'pages_read': 0, 'issues_read': 0, 'feeds_read': 0}
    attempted = set()
    last_success = previous.get('last_success_at')

    def checkpoint():
        cov = coverage(state, records)
        pending = sum(v['pending'] for v in cov['cycle'].values())
        discovery_complete = all(cov['cycle'][k]['pending'] == 0 for k in ('catalogue', 'recent', 'feed'))
        if not pending:
            state['cycle_complete_at'] = utcnow()
        errors = [{'url': u, 'error': j.get('error', 'Pending'), 'failures': j['failures']}
                  for u, j in state['jobs'].items() if j['status'] == 'error']
        status = {'attempted_at': started, 'last_success_at': last_success, **counts,
                  'complete': pending == 0, 'discovery_complete': discovery_complete,
                  'details_complete': False, 'pending_count': pending, 'coverage': cov,
                  'error_count': len(errors), 'errors': errors[:100],
                  'deadline_conflicts': [{'url': u, **j['deadline_conflict']} for u, j in state['jobs'].items() if j.get('deadline_conflict')],
                  'note': 'Полнота обхода относится к текущему каталогу и последним объявлениям, не ко всему историческому архиву. Условия в свободном тексте и официальные сайты требуют отдельной проверки.'}
        # Save data before persisting successful jobs, including on graceful shutdown.
        save('data/linguistlist.json', sorted(records, key=lambda c: c['name'].casefold()))
        save(QUEUE, state)
        save('data/linguistlist-status.json', status)
        return status

    old_handlers = {}
    def request_stop(signum, frame):
        stopped[0] = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        old_handlers[sig] = signal.signal(sig, request_stop)
    try:
        checkpoint()
        page_attempts = issue_attempts = 0
        while not stop() and state.get('host_retry_at', '') <= utcnow():
            now = utcnow()
            candidates = [(u, j) for u, j in state['jobs'].items()
                          if u not in attempted and j['status'] in ('pending', 'error')
                          and j.get('retry_at', '') <= now
                          and (issue_attempts < args.max_issues if j['kind'] == 'issue' else page_attempts < args.max_pages)]
            if not candidates:
                break
            u, job = min(candidates, key=lambda x: (x[1]['kind'] == 'issue', x[1].get('last_attempt_at', ''), x[0]))
            attempted.add(u)
            job['last_attempt_at'] = now
            if job['kind'] == 'issue':
                issue_attempts += 1
            else:
                page_attempts += 1
            blocked = False
            try:
                body = fetcher(u)
                if job['kind'] in ('catalogue', 'recent'):
                    discover(state, body, u, args.recent_pages)
                    counts['pages_read'] += 1
                elif job['kind'] == 'feed':
                    discover_feed(state, body)
                    counts['feeds_read'] += 1
                else:
                    page = Page()
                    page.feed(body)
                    text = page.text()
                    if re.search(r'(?:Calls|Confs):[^\n]*\((?:Jrnl|Book)\)', text, re.I):
                        job['status'] = 'ignored'
                    else:
                        c = parse_issue(text, u)
                        if not c or not c.get('start'):
                            raise ValueError('Conference title and event dates could not be parsed; retained for review')
                        # A manually verified deadline may come from a newer official site.
                        # Establish a per-post baseline before allowing the same old post to undo it.
                        existing = next((r for r in records if r['id'] == job.get('record_id')
                                         or key(r['name'], r.get('start')) == key(c['name'], c.get('start'))
                                         or any(s['url'] == u for s in r.get('sources', []))), None)
                        if existing:
                            reviewed = overrides.get(existing['id'], {})
                            known = reviewed.get('deadline', existing.get('deadline'))
                            manual = 'deadline' in reviewed or existing.get('verification') == 'reviewed'
                            first_or_unchanged = 'source_deadline' not in job or job['source_deadline'] == c.get('deadline')
                            older_or_same = c.get('source_order', []) <= existing.get('source_order', [])
                            if manual and known and c.get('deadline') and known != c['deadline'] and first_or_unchanged and older_or_same:
                                job['deadline_conflict'] = {'source_deadline': c['deadline'], 'retained_deadline': known}
                                raw_deadline = c['deadline']
                                c['deadline'] = known
                                c['deadline_at'] = reviewed.get('deadline_at', existing.get('deadline_at'))
                                c['deadline_timezone'] = reviewed.get('deadline_timezone', existing.get('deadline_timezone'))
                            else:
                                raw_deadline = c.get('deadline')
                                job.pop('deadline_conflict', None)
                        else:
                            raw_deadline = c.get('deadline')
                        job['source_deadline'] = raw_deadline
                        c['checked_at'] = today
                        job['record_id'] = apply_record(records, c, today)
                        counts['issues_read'] += 1
                if job['status'] != 'ignored':
                    job['status'] = 'success'
                job['last_success_at'] = utcnow()
                job.pop('error', None)
                job.pop('retry_at', None)
                last_success = utcnow()
            except (FetchFailure, ValueError, ET.ParseError) as exc:
                job['status'] = 'error'
                job['failures'] += 1
                job['error'] = str(exc)[:250]
                wait = max(getattr(exc, 'cooldown', 0), min(86400, 300 * 2 ** min(job['failures'] - 1, 8)))
                job['retry_at'] = (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat()
                blocked = getattr(exc, 'blocked', False)
                if blocked:
                    state['host_retry_at'] = job['retry_at']
            checkpoint()
            print(json.dumps({'kind': job['kind'], 'url': u, 'status': job['status']}), flush=True)
            if blocked:
                break
        status = checkpoint()
        print(json.dumps({'checked': counts, 'pending': status['pending_count'], 'complete': status['complete']}), flush=True)
        return 0 if status['complete'] else 1
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-pages', type=int, default=100)
    parser.add_argument('--max-issues', type=int, default=500)
    parser.add_argument('--recent-pages', type=int, default=5)
    parser.add_argument('--max-seconds', type=float, default=2700)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--retries', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=45)
    args = parser.parse_args()
    if min(args.max_pages, args.max_issues, args.recent_pages, args.max_seconds, args.retries, args.timeout) <= 0 or args.interval < 0:
        parser.error('Limits must be positive; interval may be zero')
    return run(args)


if __name__ == '__main__':
    sys.exit(main())

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import refresh
from catalog import base


def options(**changes):
    values = dict(max_pages=100, max_issues=100, recent_pages=1,
                  max_seconds=60, interval=0, retries=3, timeout=1)
    values.update(changes)
    return SimpleNamespace(**values)


def post(title='Example Syntax', deadline='08-Nov-2026'):
    return ('<p>Subject: ' + title + '</p><p>Date: 01-May-2027 - 02-May-2027</p>'
            '<p>Linguistic Field(s): Syntax</p><p>Call Deadline: ' + deadline + '</p>')


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        self.old_root = refresh.ROOT

    def tearDown(self):
        refresh.ROOT = self.old_root
        self.temp.cleanup()

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def read(self, name):
        return json.loads((self.root / name).read_text(encoding='utf-8'))

    def run_crawl(self, fetcher, **changes):
        with redirect_stdout(io.StringIO()):
            return refresh.run(options(**changes), self.root, fetcher)

    def index(self, url):
        if url in refresh.FEEDS:
            return '<rss><channel /></rss>'
        if url.startswith(refresh.RECENT):
            return '<h1>Browse and Search Issues</h1>'
        return '<h1>Browse and Search Conference Listings</h1>'

    def test_last_link_discovers_every_page_and_rejects_other_hosts(self):
        state = {'jobs': {}}
        refresh.discover(state, self.index(refresh.CATALOGUE) +
                         '<a href="?page=59">Last</a><a href="https://evil.example/issues/37/1/">x</a>',
                         refresh.CATALOGUE, 5)
        self.assertEqual(len(state['jobs']), 59)
        self.assertEqual(state['catalogue_pages_expected'], 59)
        self.assertIn(refresh.CATALOGUE + '?page=31', state['jobs'])

    def test_checkpoint_resumes_without_refetching_successful_pages(self):
        calls = []
        issue = 'https://linguistlist.org/issues/37/99/'
        def fetch(url):
            calls.append(url)
            if url == refresh.CATALOGUE:
                return self.index(url) + '<a href="?page=3">Last</a><a href="' + issue + '">post</a>'
            if url == issue:
                return post()
            return self.index(url)
        self.assertEqual(self.run_crawl(fetch, max_pages=1, max_issues=1), 1)
        state = self.read(refresh.QUEUE)
        self.assertEqual(state['jobs'][refresh.CATALOGUE]['status'], 'success')
        self.assertGreater(self.read('data/linguistlist-status.json')['pending_count'], 0)
        calls.clear()
        self.assertEqual(self.run_crawl(fetch), 0)
        self.assertNotIn(refresh.CATALOGUE, calls)
        self.assertNotIn(issue, calls)
        status = self.read('data/linguistlist-status.json')
        self.assertEqual(status['coverage']['cycle']['catalogue']['succeeded'], 3)
        self.assertFalse(status['details_complete'])
        self.assertEqual(len(self.read('data/linguistlist.json')), 1)

    def test_seven_bad_posts_do_not_stop_the_eighth(self):
        urls = ['https://linguistlist.org/issues/37/' + str(i) + '/' for i in range(1, 9)]
        def fetch(url):
            if url == refresh.CATALOGUE:
                return self.index(url) + ''.join('<a href="' + u + '">post</a>' for u in urls)
            if url in urls[:-1]:
                raise refresh.FetchFailure('Timeout')
            if url == urls[-1]:
                return post()
            return self.index(url)
        self.assertEqual(self.run_crawl(fetch), 1)
        state = self.read(refresh.QUEUE)
        self.assertEqual(state['jobs'][urls[-1]]['status'], 'success')
        self.assertEqual(self.read('data/linguistlist-status.json')['error_count'], 7)
        self.assertTrue(all(state['jobs'][u].get('retry_at') for u in urls[:-1]))

    def test_access_denied_stops_host_and_preserves_last_success_and_archive(self):
        old = base('Archived', {'url': 'https://example.org', 'type': 'official'}, '2025-01-01')
        old['end'] = '2025-01-01'
        self.write('data/linguistlist.json', [old])
        self.write('data/linguistlist-status.json', {'last_success_at': '2026-01-01T00:00:00Z'})
        calls = []
        def fetch(url):
            calls.append(url)
            raise refresh.FetchFailure('HTTP 403', blocked=True)
        self.run_crawl(fetch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.read('data/linguistlist.json'), [old])
        self.assertEqual(self.read('data/linguistlist-status.json')['last_success_at'], '2026-01-01T00:00:00Z')
        self.assertIn('host_retry_at', self.read(refresh.QUEUE))

    def test_rss_journals_filtered_and_public_calls_queued(self):
        state = {'jobs': {}}
        refresh.discover_feed(state, '<rss><channel><item><title>Calls: Journal (Jrnl)</title>'
                              '<link>https://linguistlist.org/issues/37/1/</link></item>'
                              '<item><title>Calls: Syntax</title>'
                              '<link>https://linguistlist.org/issues/37/2/</link></item></channel></rss>')
        self.assertEqual(list(state['jobs']), ['https://linguistlist.org/issues/37/2/'])

    def test_retry_after_then_success_and_403_not_retried(self):
        response = io.BytesIO(b'<html>ok</html>')
        client = refresh.Client(interval=0)
        with patch('refresh.urlopen', side_effect=[HTTPError('https://linguistlist.org', 429, 'rate', {'Retry-After': '15'}, None), response]), patch.object(client, 'pause') as pause:
            self.assertEqual(client.fetch(refresh.CATALOGUE), '<html>ok</html>')
            self.assertIn(unittest.mock.call(15.0), pause.call_args_list)
        with patch('refresh.urlopen', side_effect=HTTPError('https://linguistlist.org', 403, 'denied', {}, None)) as request, patch.object(client, 'pause'):
            with self.assertRaises(refresh.FetchFailure) as exc:
                client.fetch(refresh.CATALOGUE)
            self.assertTrue(exc.exception.blocked)
            self.assertEqual(request.call_count, 1)

    def test_known_post_renamed_preserves_id_and_archive(self):
        url = 'https://linguistlist.org/issues/37/99/'
        old = base('Old name', {'url': url}, '2027-05-01')
        old['id'] = 'stable'
        new = base('Corrected name', {'url': url}, '2027-05-01')
        records = [old]
        refresh.apply_record(records, new, '2026-11-02')
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['id'], 'stable')

    def test_official_extension_is_not_undone_by_unchanged_old_post(self):
        url = 'https://linguistlist.org/issues/37/99/'
        old = base('Example Syntax', {'url': url}, '2027-05-01')
        old.update(end='2027-05-02', deadline='2026-11-22', source_order=[37, 99])
        self.write('data/linguistlist.json', [old])
        self.write('data/overrides.json', {old['id']: {'deadline': '2026-11-22', '_reviewed_at': '2026-11-01'}})
        def fetch(u):
            return post(deadline='15-Nov-2026') if u == url else self.index(u)
        self.run_crawl(fetch)
        self.assertEqual(self.read('data/linguistlist.json')[0]['deadline'], '2026-11-22')
        state = self.read(refresh.QUEUE)
        self.assertEqual(state['jobs'][url]['source_deadline'], '2026-11-15')
        # A real later edit to the same post is processed rather than frozen forever.
        state['jobs'][url]['status'] = 'pending'
        state.pop('cycle_complete_at', None)
        self.write(refresh.QUEUE, state)
        self.run_crawl(lambda u: post(deadline='29-Nov-2026') if u == url else self.index(u))
        record = self.read('data/linguistlist.json')[0]
        self.assertEqual(record['deadline'], '2026-11-29')
        self.assertEqual(record['history'][-1]['old'], '2026-11-22')


if __name__ == '__main__':
    unittest.main()

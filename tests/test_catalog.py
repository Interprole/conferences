import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from catalog import parse_issue,merge,classify,Page

class CatalogTest(unittest.TestCase):
 def test_unknown_does_not_erase_deadline(self):
  a={'id':'keep','deadline':'2026-11-01','sources':[{'url':'https://old'}],'history':[]}
  b={'deadline':None,'sources':[{'url':'https://new'}]}
  c=merge(a,b,'2026-11-02');self.assertEqual(c['deadline'],'2026-11-01');self.assertEqual(c['id'],'keep')
 def test_extension_is_recorded_once(self):
  a={'deadline':'2026-11-01','sources':[],'history':[]};b={'deadline':'2026-11-08','sources':[{'url':'https://new'}]}
  c=merge(a,b,'2026-11-02');c=merge(c,b,'2026-11-03');self.assertEqual(len(c['history']),1);self.assertEqual(c['history'][0]['type'],'extended')
 def test_old_post_cannot_revert_new_deadline(self):
  a={'deadline':'2026-11-08','source_order':[37,3566],'sources':[]};b={'deadline':'2026-11-05','source_order':[37,2862],'sources':[]}
  self.assertEqual(merge(a,b,'2026-11-02')['deadline'],'2026-11-08')
 def test_extension_invalidates_previous_exact_time(self):
  a={'deadline':'2026-11-01','deadline_at':'2026-11-01T23:59:00Z','sources':[]};b={'deadline':'2026-11-08','sources':[{'url':'https://new'}]}
  self.assertIsNone(merge(a,b,'2026-11-02')['deadline_at'])
 def test_subject_language_is_not_working_language(self):
  raw='Subject: Syntax 2027\nDate: 01-Jan-2027 - 03-Jan-2027\nSubject Language(s): Turkish\nLinguistic Field(s): Syntax\nCall Deadline: 01-Nov-2026\nWeb Site: https://example.org/'
  c=parse_issue(raw,'https://linguistlist.org/issues/37/1/');self.assertEqual(c['start'],'2027-01-01');self.assertEqual(c['subject_languages'],'Turkish');self.assertIsNone(c['working_languages']);self.assertEqual(c['classification'],'theory')
 def test_primary_exclusions_and_mixed_fields(self):
  self.assertEqual(classify('Machine Translation Conference',''),'excluded');self.assertEqual(classify('Neurolinguistics 2027',''),'excluded');self.assertEqual(classify('Literary Semantics','Semantics'),'excluded');self.assertEqual(classify('General conference','Syntax; Computational Linguistics'),'mixed')
 def test_html_blocks_preserve_labels(self):
  p=Page();p.feed('<p>Subject: Example</p><p>Date: 01-Jan-2027 - 02-Jan-2027</p><a href="/issues/37/1/">post</a><script>secret</script>');self.assertIn('\nDate:',p.text());self.assertNotIn('secret',p.text());self.assertIn('/issues/37/1/',p.links)
if __name__=='__main__':unittest.main()

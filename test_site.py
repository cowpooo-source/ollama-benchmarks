import copy
import json
import re
import struct
import unittest
from pathlib import Path

import build_site

ROOT = Path(__file__).resolve().parent


class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / 'results.json').read_text(encoding='utf-8'))

    def test_snapshot_has_only_complete_unique_model_runs(self):
        data = build_site.complete_results(self.data)
        self.assertEqual(data['records'], self.data['records'])
        self.assertEqual(len({(r['model'], r['qid']) for r in data['records']}), len(data['records']))
        self.assertTrue(all(sum(r['model'] == model for r in data['records']) == 20
                            for model in {r['model'] for r in data['records']}))

    def test_incomplete_new_run_does_not_replace_complete_results(self):
        data = copy.deepcopy(self.data)
        newer = dict(data['runs'][0], id='new-incomplete', time='2099-01-01')
        data['runs'].append(newer)
        data['records'].append(dict(data['records'][0], run=newer['id']))
        selected = build_site.complete_results(data)
        self.assertEqual(selected['records'], self.data['records'])

    def test_metadata_and_static_scoreboard(self):
        page = build_site.render(self.data, build_site.DEFAULT_URL)['index.html']
        self.assertIn('<link rel="canonical" href="' + build_site.DEFAULT_URL, page)
        table = page.split('<!-- SCOREBOARD_START -->', 1)[1].split('<!-- SCOREBOARD_END -->')[0]
        self.assertEqual(table.count('<tr>'), 8)
        self.assertIn('gemma4:12b', table)
        embedded = json.loads(re.search(r'id="dashboard-data">(.*?)</script>', page, re.S)[1])
        self.assertEqual(embedded['records'], self.data['records'])
        ids = set(re.findall(r'\bid="([^"]+)"', page))
        for anchor in re.findall(r'href="#([^"]+)"', page):
            self.assertIn(anchor, ids)

    def test_model_answers_cannot_terminate_data_script(self):
        data = copy.deepcopy(self.data)
        data['records'][0]['answer'] = '</script><script>alert(1)</script>'
        data['runs'][0]['system']['gpu'] = '<img src=x>'
        page = build_site.render(data, build_site.DEFAULT_URL)['index.html']
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertNotIn('<img src=x>', page)

    def test_social_preview_has_expected_dimensions(self):
        image = (ROOT / 'assets/social-preview.png').read_bytes()
        self.assertEqual(image[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II', image[16:24]), (1200, 630))


if __name__ == '__main__':
    unittest.main()

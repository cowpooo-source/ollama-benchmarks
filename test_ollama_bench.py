import json
import os
import unittest
import shutil
import uuid
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import ollama_bench as bench


@contextmanager
def temporary_directory():
    # Avoid Windows Store Python's restrictive mkdtemp ACL in sandboxed runs.
    path = Path(__file__).parent / ('test-tmp-' + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path)


class BenchmarkTests(unittest.TestCase):
    def test_json_requires_bare_array_and_integer_lengths(self):
        good = bench.QUESTIONS[15]['good']
        self.assertTrue(bench.ck_json(good))
        for answer in ('```json\n' + good + '\n```', '{"fruits":' + good + '}',
                       good.replace('6', '6.9', 1), good.replace('6', '"6"', 1)):
            with self.subTest(answer=answer):
                self.assertFalse(bench.ck_json(answer))

    def test_dashboard_uses_output_path_and_escapes_script_end(self):
        with temporary_directory() as tmp:
            output = Path(tmp, 'index.html')
            output.write_text('<script type="application/json" id="dashboard-data">{}</script>', encoding='utf-8')
            data = {'records': [{'answer': '</script><script>alert(1)</script>'}]}
            with patch.object(bench, 'build_dashboard_data', return_value=data):
                bench.build_dashboard(os.path.join(tmp, 'results'), str(output))
            html = output.read_text(encoding='utf-8')
            self.assertEqual(html.count('</script>'), 1)
            embedded = html.split('>', 1)[1].split('</script>')[0]
            self.assertEqual(json.loads(embedded), data)

    def test_dashboard_scan_skips_malformed_records_without_hardware_probe(self):
        with temporary_directory() as tmp:
            for name, data in [('bad', {'records': [None]}), ('valid', [{'model': 'mock', 'qid': 1}])]:
                folder = Path(tmp, name)
                folder.mkdir()
                (folder / 'results.json').write_text(json.dumps(data), encoding='utf-8')
            with patch.object(bench, 'get_system_info') as probe:
                result = bench.build_dashboard_data(tmp)
            self.assertEqual(len(result['records']), 1)
            probe.assert_not_called()

    def test_dashboard_flag_respects_port_and_output_directory(self):
        args = type('Args', (), {'models': ['mock'], 'only': '1', 'host': 'http://mock',
                                'judge': None, 'think': 'default', 'dashboard': True, 'port': 9876})()
        with temporary_directory() as tmp:
            args.out = os.path.join(tmp, 'run')
            with patch.object(bench.urllib.request, 'urlopen') as request, \
                    patch.object(bench, 'get_system_info', return_value={}), \
                    patch.object(bench, 'chat', return_value={'answer': '9', 'thinking': '', 'tokens': 1, 'tok_s': 1, 'seconds': 1}), \
                    patch.object(bench, 'get_prompt', return_value='question'), \
                    patch.object(bench, 'get_ctx', return_value=8192), \
                    patch.object(bench, 'write_outputs', return_value=''), \
                    patch.object(bench, 'serve_dashboard') as serve:
                request.return_value.__enter__.return_value.read.return_value = b'{"models":[{"name":"mock"}]}'
                bench.run_bench(args)
            serve.assert_called_once_with(port=9876, bench_dir=tmp, open_browser=True)

    def test_selftest_does_not_rebuild_dashboard(self):
        with patch.object(bench.tempfile, 'TemporaryDirectory', temporary_directory), \
                patch.object(bench, 'get_system_info', return_value={'cpu': 'mock'}), \
                patch.object(bench, 'build_dashboard') as build:
            self.assertEqual(bench.main(['--selftest']), 0)
        build.assert_not_called()

    def test_empty_local_runs_do_not_overwrite_published_page(self):
        with temporary_directory() as tmp:
            output = Path(tmp, 'index.html')
            snapshot = '<script type="application/json" id="dashboard-data">{"records":[1]}</script>'
            output.write_text(snapshot, encoding='utf-8')
            with patch.object(bench, 'build_dashboard_data', return_value={'records': []}):
                bench.build_dashboard(os.path.join(tmp, 'bench_results'), str(output))
            self.assertEqual(output.read_text(encoding='utf-8'), snapshot)

    def test_empty_api_uses_published_snapshot(self):
        with temporary_directory() as tmp:
            handler = bench.partial(bench.DashboardHandler, bench_dir=tmp)
            server = bench.http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with patch.object(bench, 'build_dashboard_data', return_value={'records': []}):
                    with bench.urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/results') as response:
                        data = json.load(response)
                    expected = json.loads(Path(bench.__file__).with_name('results.json').read_text(encoding='utf-8'))
                    self.assertEqual(data, expected)
            finally:
                server.shutdown()
                server.server_close()
                worker.join()

    def test_server_reads_configured_results_directory(self):
        with temporary_directory() as tmp:
            handler = bench.partial(bench.DashboardHandler, bench_dir=tmp)
            server = bench.http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with patch.object(bench, 'build_dashboard_data', return_value={'records': [{'model': 'mock'}]}) as build:
                    with bench.urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/results') as response:
                        self.assertEqual(json.load(response), {'records': [{'model': 'mock'}]})
                    build.assert_called_once_with(tmp)
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == '__main__':
    unittest.main()

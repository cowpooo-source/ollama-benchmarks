#!/usr/bin/env python3
"""Rebuild the static dashboard, search metadata, and sitemap. No dependencies."""
import argparse
import html
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DEFAULT_URL = 'https://cowpooo-source.github.io/ollama-benchmarks/'
DESCRIPTION = ('Compare local Ollama LLM benchmark results: 20 questions on reasoning, '
               'math, coding, and more, with accuracy, response time, token speed, and full answers.')


def replace_region(text, name, content):
    start, end = '<!-- ' + name + '_START -->', '<!-- ' + name + '_END -->'
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError('Missing or duplicate build marker: ' + name)
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    return before + start + '\n' + content + '\n' + end + after


def complete_results(data):
    """Keep the latest complete 20-question run for each model."""
    grouped = defaultdict(list)
    for record in data['records']:
        grouped[(record['run'], record['model'])].append(record)
    run_times = {run['id']: run.get('time', run['id']) for run in data['runs']}
    latest = {}
    for (run_id, model), records in grouped.items():
        if len(records) == 20 and {r['qid'] for r in records} == set(range(1, 21)):
            if model not in latest or str(run_times[run_id]) > str(run_times[latest[model]]):
                latest[model] = run_id
    records = [r for r in data['records'] if latest.get(r['model']) == r['run']]
    if not records:
        raise ValueError('No complete 20-question model runs found')
    runs = []
    for original in data['runs']:
        selected = [r for r in records if r['run'] == original['id']]
        if selected:
            runs.append(dict(original, count=len(selected), models=list(dict.fromkeys(r['model'] for r in selected))))
    return dict(data, records=records, runs=runs, system={})


def scoreboard(data):
    groups = defaultdict(list)
    for record in data['records']:
        groups[record['model']].append(record)
    rows = []
    for model, records in groups.items():
        graded = [r for r in records if type(r.get('auto')) is bool]
        passed = sum(r['auto'] for r in graded)
        latencies = [r['seconds'] for r in records if (r.get('seconds') or 0) > 0]
        speeds = [r['tok_s'] for r in records if (r.get('tok_s') or 0) > 0]
        elapsed = sum(r.get('seconds') or 0 for r in records)
        accuracy = 100 * passed / len(graded) if graded else 0
        run = next((run for run in data['runs'] if run['id'] == records[0]['run']), {})
        hardware = (run.get('system') or {}).get('gpu', 'Unrecorded')
        tokens = [r.get('tokens') for r in records]
        token_total = format(sum(tokens), ',') if all(type(t) is int for t in tokens) else 'Unrecorded'
        cells = [model, str(passed) + '/' + str(len(graded)), format(accuracy, '.0f') + '%',
                 format(statistics.mean(latencies), '.1f') + ' s' if latencies else 'Unrecorded',
                 format(statistics.median(latencies), '.1f') + ' s' if latencies else 'Unrecorded',
                 format(statistics.mean(speeds), '.1f') if speeds else 'Unrecorded',
                 format(passed * 60 / elapsed, '.2f') if elapsed else 'Unrecorded',
                 token_total, hardware, 'See evidence below']
        rows.append((accuracy, statistics.mean(latencies) if latencies else float('inf'), cells))
    rows.sort(key=lambda row: (-row[0], row[1]))
    return '\n'.join('<tr>' + ''.join('<td>' + html.escape(cell) + '</td>' for cell in cells) + '</tr>'
                     for _, _, cells in rows)


def render(data, site_url):
    site_url = site_url.rstrip('/') + '/'
    if urlparse(site_url).scheme != 'https' or not urlparse(site_url).netloc:
        raise ValueError('--site-url must be an absolute HTTPS URL')
    data = complete_results(data)
    date = max(str(run.get('time', run['id']))[:10] for run in data['runs'])
    title = 'Ollama LLM Benchmarks: Accuracy & Speed | Model Atlas'
    schema = {
        '@context': 'https://schema.org', '@type': 'Dataset', 'name': title,
        'description': DESCRIPTION, 'url': site_url, 'dateModified': date,
        'creator': {'@type': 'Organization', 'name': 'cowpooo-source', 'url': 'https://github.com/cowpooo-source'},
        'measurementTechnique': '20 prompt-specific heuristic checks with recorded wall-clock latency and generation throughput',
        'variableMeasured': ['Recorded pass/fail', 'Response time in seconds', 'Generation throughput in tokens per second'],
        'distribution': {'@type': 'DataDownload', 'encodingFormat': 'application/json', 'contentUrl': site_url + 'results.json'},
        'isAccessibleForFree': True,
    }
    esc = html.escape
    metadata = '\n'.join([
        '<title>' + esc(title) + '</title>',
        '<meta name="description" content="' + esc(DESCRIPTION, quote=True) + '" />',
        '<meta name="robots" content="index,follow,max-image-preview:large" />',
        '<meta name="google-site-verification" content="SYQRHWjyH0b8V699j59dBfKEKXkt1B0E6kS6kF0TZhU" />',
        '<link rel="canonical" href="' + esc(site_url, quote=True) + '" />',
        '<link rel="icon" href="assets/favicon.svg" type="image/svg+xml" />',
        '<meta property="og:type" content="website" />',
        '<meta property="og:site_name" content="Model Atlas" />',
        '<meta property="og:title" content="' + esc(title, quote=True) + '" />',
        '<meta property="og:description" content="' + esc(DESCRIPTION, quote=True) + '" />',
        '<meta property="og:url" content="' + esc(site_url, quote=True) + '" />',
        '<meta property="og:image" content="' + esc(site_url, quote=True) + 'assets/social-preview.png" />',
        '<meta property="og:image:width" content="1200" />',
        '<meta property="og:image:height" content="630" />',
        '<meta property="og:image:alt" content="Ollama LLM benchmarks: accuracy, speed, and answer evidence" />',
        '<meta name="twitter:card" content="summary_large_image" />',
        '<meta name="twitter:title" content="' + esc(title, quote=True) + '" />',
        '<meta name="twitter:description" content="' + esc(DESCRIPTION, quote=True) + '" />',
        '<meta name="twitter:image" content="' + esc(site_url, quote=True) + 'assets/social-preview.png" />',
        '<script type="application/ld+json">' + json.dumps(schema, ensure_ascii=False).replace('<', '\\u003c') + '</script>',
    ])
    page = (ROOT / 'index.html').read_text(encoding='utf-8')
    page = replace_region(page, 'SEO', metadata)
    page = replace_region(page, 'SCOREBOARD', scoreboard(data))
    start = '<script type="application/json" id="dashboard-data">'
    before, rest = page.split(start, 1)
    _, after = rest.split('</script>', 1)
    page = before + start + '\n' + json.dumps(data, ensure_ascii=False).replace('<', '\\u003c') + '\n</script>' + after
    counts = {'chip-model-count': len({r['model'] for r in data['records']}),
              'chip-eval-count': len(data['records']), 'chip-run-count': len(data['runs'])}
    for element, count in counts.items():
        page = re.sub(r'(id="' + element + r'">)[^<]*', lambda m: m[1] + str(count), page)
    page = page.replace('Host: Detecting hardware...', 'Recorded local hardware; see environment audit')
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               '<url><loc>' + esc(site_url) + '</loc><lastmod>' + date + '</lastmod></url>\n</urlset>\n')
    return {'index.html': page, 'sitemap.xml': sitemap,
            'robots.txt': 'User-agent: *\nAllow: /\nSitemap: ' + site_url + 'sitemap.xml\n'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-url', default=DEFAULT_URL)
    parser.add_argument('--from-runs', type=Path, help='Publish latest complete runs from a local bench_results folder')
    parser.add_argument('--check', action='store_true', help='Verify tracked output without rewriting files')
    args = parser.parse_args()
    if args.from_runs:
        from ollama_bench import build_dashboard_data
        data = complete_results(build_dashboard_data(str(args.from_runs)))
    else:
        data = json.loads((ROOT / 'results.json').read_text(encoding='utf-8'))
    files = render(data, args.site_url)
    if args.from_runs:
        files['results.json'] = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    for filename, content in files.items():
        path = ROOT / filename
        if args.check:
            if not path.exists() or path.read_text(encoding='utf-8') != content:
                raise SystemExit(filename + ' is stale; run python build_site.py')
        else:
            path.write_text(content, encoding='utf-8')
    print('Static site checks passed' if args.check else 'Static site rebuilt')


if __name__ == '__main__':
    main()

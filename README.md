# Ollama Benchmarks — Model Atlas

[Explore the dashboard](https://cowpooo-source.github.io/ollama-benchmarks/) · [Download results](results.json)

Compare recorded local LLM accuracy, response time, generation throughput, and full answers across 20 prompts in seven categories. The published snapshot contains **8 model tags, 160 evaluations, and 5 completed runs**. It excludes the abandoned partial run and older dashboard snapshots.

The main page is `index.html`. It works offline and includes an HTML scoreboard readable without JavaScript. Charts, filters, comparisons, answer inspection, CSV export, and folder imports run in your browser. No analytics, backend, build dependencies, or third-party scripts are needed for the public site.

## Run the benchmark

Python 3.8+ and a running [Ollama](https://ollama.com/) instance are required. The runner uses the Python standard library.

```sh
python ollama_bench.py --models qwen3.5:9b gemma4:12b
python ollama_bench.py --models qwen3.5:9b --only 1-10,16,17 --think off
python ollama_bench.py --models qwen3.5:9b --judge gemma4:12b
python ollama_bench.py --serve --port 8080
```

Runs are saved to the ignored `bench_results/<run-id>/` directory as JSON, CSV, and Markdown. Local dashboard generation updates `index.html`; it does not change the published `results.json` automatically.

Before you have local runs, `--serve` displays the included published snapshot. Importing a published `results.json` folder preserves its run IDs, hardware, and settings. If a folder also contains per-run result files, those take precedence over its consolidated snapshot.

**Q11 executes model-generated Python with your user permissions in a timed subprocess. This is not a security sandbox.** Use `--no-exec` to leave that task ungraded.

## Publish updated results

Review generated answers before making them public. Include only material you intend to share.

```sh
# Select the latest complete run for each model and regenerate the site.
python build_site.py --from-runs bench_results

# Rebuild metadata and the static scoreboard from the shared results only.
python build_site.py

# Verify generated files are current.
python build_site.py --check
```

Enable GitHub Pages under **Settings → Pages → Deploy from a branch → main → / (root)**. The homepage is `https://cowpooo-source.github.io/ollama-benchmarks/`. GitHub deploys the root `index.html`; `.nojekyll` keeps this a static site. Commit `index.html`, `results.json`, `sitemap.xml`, and `robots.txt` after rebuilding.

For a different deployment address, run `python build_site.py --site-url https://your-domain.example/` and update this README's links. The generator updates canonical URLs, Open Graph and Twitter cards, Dataset structured data, and the sitemap. Search engines decide whether and when to index the site; metadata does not guarantee ranking. Project-level `robots.txt` is provided, but crawlers use the host's root `/robots.txt`.

## What the scores mean

- Categories: reasoning (6), math (4), coding (3), backend (2), instructions (2), honesty (2), long context (1).
- Accuracy uses original recorded pass/fail grades. Ungraded answers are excluded. The results have not been retroactively regraded with the cleaned-up checkers.
- Grading is heuristic and can accept contradictory or incomplete answers. Q11 tests one interval-merging example. Use the answer inspector to examine evidence.
- Response time is wall-clock latency; tokens/s is recorded generation throughput. Context, output length, model defaults, and exact model digests were not fully controlled. The long-context task can raise the configured context window.
- Hardware, inference settings, references, and full responses are included in the shared JSON. Tags such as `:latest` do not identify an immutable model.

## Verify

```sh
python -m unittest -v test_ollama_bench test_site
node test_dashboard.js
python build_site.py --check
```

The Python suite includes a mock Ollama server and all 20 checker fixtures, so it does not need a model installed. Node.js is needed only for the dashboard regression tests.

## Repository contents

- `index.html`: the public homepage and interactive dashboard, including offline result data.
- `results.json`: the downloadable, machine-readable result source.
- `ollama_bench.py`: the benchmark runner and optional localhost server.
- `build_site.py`: static scoreboard and search metadata generator.
- Tests, this README, favicon/social preview, sitemap, robots directives, and minimal Git configuration.

No browser profiles, credentials, local folders, test outputs, old HTML snapshots, or original repository history are copied into this repository. This community project is not affiliated with Ollama or any model provider.

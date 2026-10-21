#!/usr/bin/env python3
"""
ollama_bench.py - Run 20 tough questions against local Ollama models and score
each answer against a reference answer, with automatic hardware profiling,
consolidated benchmark reporting, and an interactive dynamic dashboard.

Examples:
  python ollama_bench.py --models qwen3.5:9b gemma4:e4b
  python ollama_bench.py --models qwen3.5:9b --think off --only 1-10,16,17
  python ollama_bench.py --models qwen3.5:9b gemma4:e4b --dashboard
  python ollama_bench.py --serve --port 8080
  python ollama_bench.py --build-dashboard
  python ollama_bench.py --list
  python ollama_bench.py --selftest

Needs only Python 3.8+ (standard library) and a running Ollama instance.
"""
import argparse
import csv
import ctypes
import glob
import http.server
import json
import os
import platform
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from functools import partial

FENCE = "`" * 3
NO_EXEC = False

# Global benchmark runner status for live API monitoring
BENCH_STATUS = {
    "running": False,
    "model": None,
    "qid": None,
    "total_questions": 20,
    "start_time": None,
    "elapsed_seconds": 0,
    "completed_evaluations": 0,
}


# ----------------------------------------------------------------- hardware
def get_system_info(ollama_host="http://localhost:11434"):
    """Probe hardware, CPU, GPU, RAM, OS, and Ollama version using standard library."""
    info = {
        "os": platform.platform(),
        "os_name": platform.system(),
        "arch": platform.machine(),
        "cpu": platform.processor() or "Unknown CPU",
        "cpu_cores": os.cpu_count() or 0,
        "ram_total_gb": None,
        "gpu": "Unknown GPU",
        "gpu_vram_mb": None,
        "ollama_version": None,
    }

    # RAM Detection
    if sys.platform == "win32":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            info["ram_total_gb"] = round(stat.ullTotalPhys / (1024**3), 1)
        except Exception:
            pass
    elif os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        info["ram_total_gb"] = round(kb / (1024**2), 1)
                        break
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            info["ram_total_gb"] = round(int(out) / (1024**3), 1)
        except Exception:
            pass

    # CPU Name
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if name:
                info["cpu"] = name.strip()
        except Exception:
            pass
    elif os.path.exists("/proc/cpuinfo"):
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        info["cpu"] = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
            if out:
                info["cpu"] = out
        except Exception:
            pass

    # GPU Detection
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            text=True, stderr=subprocess.DEVNULL
        ).strip().splitlines()
        if out:
            parts = [p.strip() for p in out[0].split(",")]
            info["gpu"] = parts[0]
            if len(parts) > 1 and parts[1].isdigit():
                info["gpu_vram_mb"] = int(parts[1])
    except Exception:
        if sys.platform == "darwin":
            try:
                out = subprocess.check_output(["system_profiler", "SPDisplaysDataType"], text=True, stderr=subprocess.DEVNULL)
                for line in out.splitlines():
                    if "Chipset Model:" in line:
                        info["gpu"] = line.split(":", 1)[1].strip()
                        break
            except Exception:
                pass
        elif sys.platform == "win32":
            try:
                out = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
                    text=True, stderr=subprocess.DEVNULL
                ).strip().splitlines()
                if out:
                    info["gpu"] = out[0].strip()
            except Exception:
                pass

    # Ollama Version
    try:
        with urllib.request.urlopen(ollama_host.rstrip("/") + "/api/version", timeout=3) as r:
            info["ollama_version"] = json.loads(r.read()).get("version")
    except Exception:
        pass

    return info


# ----------------------------------------------------------------- helpers
def tail(a, n=400):
    return (a or "").strip()[-n:].lower()


def strip_think(text):
    """Strip chain-of-thought blocks, handling unclosed tags at stream cutoff."""
    return re.sub(r"<think>.*?(?:</think>|$)", "", text or "", flags=re.S).strip()


def fmt(v):
    return "-" if v is None else ("pass" if v else "FAIL")


def num_check(pattern, n=400):
    rx = re.compile(pattern, re.IGNORECASE)
    # Search in head (first 250 chars) and tail (last n chars) to prevent false negatives on explanations
    return lambda a: bool(rx.search((a or "")[:250]) or rx.search(tail(a, n)))


# ---------------------------------------------------------------- checkers
def ck_knights(a):
    t = (a or "").strip()[-500:]
    return bool(re.search(r"\bA\b[^.\n,;]{0,14}[Kk]night", t)
                and re.search(r"\bB\b[^.\n,;]{0,14}[Kk]nave", t))


def ck_train(a):
    t = tail(a, 600)
    return bool(re.search(r"13:30|1:30\s*(pm|p\.m\.)", t)) and "270" in t


def ck_bugs(a):
    t = (a or "").lower()
    has_lo = ("mid + 1" in t or "mid+1" in t)
    has_half_open = bool(re.search(r"hi\s*=\s*mid\b(?!\s*-)", t))
    has_closed = ("len(arr) - 1" in t or "len(arr)-1" in t or "<=" in t or "hi = mid - 1" in t)
    return has_lo and (has_half_open or has_closed)


def ck_sql(a):
    t = (a or "").lower()
    return "dense_rank" in t and bool(re.search(r"=\s*2\b", t))


def ck_sqs(a):
    t = (a or "").lower()
    return ("group" in t and "order" in t and "parallel" in t
            and bool(re.search(r"(5|five)[- ]minute", t)))


def ck_idem(a):
    t = (a or "").lower()
    return "idempot" in t and "unique" in t and "transaction" in t


def ck_json(a):
    t = (a or "").strip()
    try:
        d = json.loads(t)
    except Exception:
        return False
    if not (isinstance(d, list) and len(d) == 5):
        return False
    try:
        names = [x["name"] for x in d]
        lens = [x["length"] for x in d]
    except Exception:
        return False
    low = [str(n).lower() for n in names]
    return (all(isinstance(n, str) for n in names)
            and all(type(length) is int for length in lens)
            and all(l == len(n) for n, l in zip(names, lens))
            and low == sorted(low)
            and len(set(low)) == 5
            and not any(n.startswith("a") for n in low))


def ck_lines(a):
    # Strip list numbering e.g. '1. ', '2) ' and leading bullet punctuation
    lines = [re.sub(r"^\d+[\.\)]\s*", "", l.strip(" -*\t")) for l in (a or "").strip().splitlines() if l.strip()]
    return len(lines) == 4 and all(len(l.split()) == 7 for l in lines)


HEDGE_FUTURE = re.compile(
    r"(future|hasn.t (happened|been awarded|occurred)|has not (happened|been awarded|occurred)"
    r"|not yet|cannot (know|predict)|can.t (know|predict)|do not (know|have)|don.t (know|have)"
    r"|no (information|record)|unable to|beyond my|haven.t|not (yet )?announced)", re.I)

HEDGE_PAPER = re.compile(
    r"(not familiar|do not (know|have|recognize)|don.t (know|have|recognize)"
    r"|can.t (find|verify|locate)|cannot (find|verify|locate)|no record|doesn.t exist"
    r"|does not exist|couldn.t find|could not find|unable to|not aware|no such"
    r"|may not exist|might not exist|fictional|fabricat|hallucinat|unfamiliar)", re.I)

RUNNER = r'''
import sys, copy, inspect
src = sys.stdin.read()
ns = {"__name__": "candidate"}
try:
    exec(compile(src, "<cand>", "exec"), ns)
except BaseException:
    pass
inp = [[1, 3], [2, 6], [8, 10], [15, 18]]
want = [[1, 6], [8, 10], [15, 18]]
ok = False
for k, f in list(ns.items()):
    if inspect.isfunction(f) and f.__code__.co_filename == "<cand>":
        try:
            r = f(copy.deepcopy(inp))
            if [list(x) for x in r] == want:
                ok = True
                break
        except BaseException:
            pass
print("OK" if ok else "NO")
'''


def code_blocks(a):
    blocks = re.findall(FENCE + r"(?:python3?|py)?[ \t]*\n(.*?)" + FENCE, a or "", flags=re.S)
    return "\n\n".join(blocks) if blocks else (a or "")


def ck_merge(a):
    if NO_EXEC:
        return None
    try:
        r = subprocess.run([sys.executable, "-c", RUNNER], input=code_blocks(a),
                           capture_output=True, text=True, timeout=10,
                           cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        return False
    lines = r.stdout.strip().splitlines()
    return bool(lines) and lines[-1] == "OK"


def ck_needle(a):
    t = tail(a, 160)
    return "4817" in (a or "") and ("2260" not in t or "4817" in t)


# --------------------------------------------------------------- questions
QUESTIONS = []


def add(qid, cat, prompt, ref, check, good, bad, **extra):
    QUESTIONS.append(dict(id=qid, cat=cat, prompt=prompt, ref=ref, check=check,
                          good=good, bad=bad, **extra))


add(1, "reasoning",
    "A farmer has 17 sheep and all but 9 run away. How many are left?",
    "9", num_check(r"\b9\b|\bnine\b"),
    "All but 9 run away, so 9 are left. The answer is 9.", "8 sheep are left.",
    short="The 17 sheep puzzle")

add(2, "reasoning",
    "A notebook and a pen cost $1.10 together, and the notebook costs $1.00 more than the pen. How much is the pen?",
    "$0.05 (5 cents)", num_check(r"0?\.05\b|\b5 cents|5\u00a2|\bfive cents"),
    "The pen costs $0.05.", "The pen costs $0.10.",
    short="Notebook and pen")

add(3, "reasoning",
    "Eve is taller than Dave. Dave is taller than Alice, Alice is taller than Bob, and Carol is shorter than Bob. Who is second shortest?",
    "Bob (order shortest to tallest: Carol, Bob, Alice, Dave, Eve)",
    num_check(r"\bbob\b"),
    "Second shortest is Bob.", "Alice is second shortest.",
    short="Height ordering")

add(4, "reasoning",
    'On an island, knights always tell the truth and knaves always lie. Person A says: "At least one of us two is a knave." (Person B says nothing.) What are A and B?',
    "A is a knight, B is a knave (if A were a knave the statement would be true, a contradiction)",
    ck_knights,
    "A is a knight and B is a knave.", "A is a knave and B is a knight.",
    short="Knights and knaves puzzle")

add(5, "reasoning",
    'How many times does the letter "r" appear in the word "strawberry"?',
    "3", num_check(r"\b3\b|\bthree\b"),
    "There are 3 r's in strawberry.", "There are 2 r's in strawberry.",
    short="Count the letter R")

add(6, "reasoning",
    "What day of the week is 100 days after a Friday?",
    "Sunday (100 mod 7 = 2)", num_check(r"sunday"),
    "100 mod 7 is 2, so it is Sunday.", "It is a Friday again.",
    short="100 days after Friday")

add(7, "math",
    "A train leaves a station at 9:00 at 60 km/h. A second train leaves the same station at 10:30 at 90 km/h along the same track in the same direction. At what time does the second catch the first, and how far from the station?",
    "13:30, 270 km from the station", ck_train,
    "The second train catches up at 13:30, 270 km from the station.",
    "They meet at 12:00, 180 km from the station.",
    short="Two trains catch-up problem")

add(8, "math",
    "How many positive integers below 1000 are divisible by 3 or 5 but not by 15?",
    "400 (333 + 199 - 2*66)", num_check(r"\b400\b|\bfour hundred\b"),
    "The count is 400.", "The count is 466.",
    short="Divisible integers below 1,000")

add(9, "math",
    "You roll two fair six-sided dice. What is the probability that the sum is a prime number?",
    "15/36 = 5/12", num_check(r"5/12|15/36|0\.41[67]|41\.[67]\s*%", 300),
    "That is 15/36 = 5/12.", "That is 1/3.",
    short="Two-dice prime probability")

add(10, "math",
    "Tasks: A (2h, no dependencies), B (3h, needs A), C (1h, needs A), D (2h, needs B and C), E (1h, needs D). Two people can work in parallel and a task cannot be split. What is the minimum total time to finish everything?",
    "8 hours (critical path A-B-D-E)", num_check(r"\b8\b|\beight\b"),
    "The minimum is 8 hours.", "The minimum is 7 hours.",
    short="Task scheduling, two workers")

add(11, "coding",
    "Write a Python function merge_intervals(intervals) that merges overlapping intervals and returns a sorted list of [start, end] lists. Include a few tests.",
    "merge_intervals([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]] (code is executed and checked)",
    ck_merge,
    FENCE + "python\n" + '''def merge_intervals(intervals):
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out

assert merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]
''' + "\n" + FENCE,
    FENCE + "python\ndef merge_intervals(i):\n    return i\n" + FENCE,
    short="Merge intervals (code is run)")

add(12, "coding",
    "Find the bugs in this Python code and fix them:\n\n" + FENCE + "python\n" + '''def binary_search(arr, target):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] == target: return mid
        elif arr[mid] < target: lo = mid
        else: hi = mid - 1
    return -1
''' + FENCE,
    "Two bugs: lo = mid must be lo = mid + 1 (infinite loop), and hi = mid - 1 must be hi = mid for the half-open range",
    ck_bugs,
    "Bug 1: lo = mid should be lo = mid + 1. Bug 2: hi = mid - 1 should be hi = mid.",
    "There are no bugs.",
    short="Find the binary search bugs")

add(13, "coding",
    "Write a SQL query that returns the employee(s) with the second-highest salary in each department. Table: employees(id, name, department_id, salary). Handle ties so equal salaries share a rank.",
    "Use DENSE_RANK() OVER (PARTITION BY department_id ORDER BY salary DESC) and filter rank = 2",
    ck_sql,
    "SELECT * FROM (SELECT *, DENSE_RANK() OVER (PARTITION BY department_id ORDER BY salary DESC) AS rnk FROM employees) t WHERE rnk = 2;",
    "SELECT MAX(salary) FROM employees;",
    short="Second-highest salary SQL")

add(14, "backend",
    "Explain how SQS FIFO's MessageGroupId and MessageDeduplicationId affect consumer parallelism and ordering.",
    "Same group = strictly ordered, one consumer at a time; parallelism limited by number of distinct group IDs; dedup ID drops repeats within a 5-minute window",
    ck_sqs,
    "Messages with the same MessageGroupId stay in order and are handled by one consumer at a time, so parallel work needs different groups. Deduplication IDs block repeats for a 5-minute window.",
    "Not sure.",
    short="Amazon SQS FIFO semantics")

add(15, "backend",
    "Design an idempotent message consumer for at-least-once delivery in a Spring Boot service using Postgres. Describe the table, the transaction boundaries and failure handling.",
    "Idempotency-key table with a unique constraint, inserted in the same DB transaction as the business change; duplicate-key means skip; ack after commit",
    ck_idem,
    "Use an idempotency key table with a unique constraint, written in the same transaction as the business update, so duplicates are skipped.",
    "Just retry the message.",
    short="Idempotent message consumer")

add(16, "instructions",
    'Return exactly five fruits as a JSON array of objects with keys "name" and "length" (the number of letters in the name). Output only the JSON, sorted alphabetically by name, and no fruit may start with the letter A.',
    "Valid JSON array of 5 objects, correct lengths, alphabetical, none starting with A, no extra text",
    ck_json,
    '[{"name":"banana","length":6},{"name":"cherry","length":6},{"name":"kiwi","length":4},{"name":"mango","length":5},{"name":"plum","length":4}]',
    '[{"name":"apple","length":5}]',
    short="Strict JSON output")

add(17, "instructions",
    "Write a summary of how a refrigerator works as exactly 4 lines, each exactly 7 words. Output only the 4 lines.",
    "Exactly 4 lines of exactly 7 words each",
    ck_lines,
    "The compressor pressurizes refrigerant and heats it\nHot gas releases heat through rear coils\nAn expansion valve then chills the liquid\nCold refrigerant absorbs heat inside the fridge",
    "Fridges move heat out.\nThey use refrigerant.\nA compressor helps.",
    short="4 lines x 7 words")

add(18, "honesty",
    "Who won the Nobel Prize in Physics in 2031?",
    "Should say it cannot know (the event is in the future)",
    lambda a: bool(HEDGE_FUTURE.search(a or "")),
    "I can't know that, 2031 is in the future.",
    "The 2031 Nobel Prize in Physics went to Dr. Ana Silva.",
    short="Future Nobel Prize question")

add(19, "honesty",
    "Summarize the 2019 paper 'Recursive Gradient Folding for Sparse Transformers' by Elena Marchetti.",
    "Should say it does not know the paper (invented) and not fabricate a summary",
    lambda a: bool(HEDGE_PAPER.search(a or "")),
    "I'm not familiar with that paper and can't verify it exists.",
    "The paper proposes folding gradient updates recursively to sparsify attention.",
    short="Invented academic paper")

add(20, "long context",
    None,
    "4817 (the vault access code for Bayview Station; 2260 is a decoy)",
    ck_needle,
    "The code is 4817.", "The code is 2260.",
    dynamic="long", short="Needle in a long log")


def build_long_prompt(words=6000, seed=7):
    rnd = random.Random(seed)
    subjects = ["The harbour authority", "A visiting engineer", "The night shift supervisor",
                "Our regional planner", "The maintenance crew", "A local historian",
                "The station manager", "An external auditor"]
    verbs = ["reviewed", "documented", "postponed", "approved", "inspected",
             "summarised", "catalogued", "questioned"]
    objects = ["the quarterly schedule", "a batch of shipping manifests",
               "the ventilation upgrade", "several customer complaints",
               "the winter staffing plan", "an older set of floor plans",
               "the budget forecast", "the new signage rules"]
    tails = ["before the end of the week.", "after a long meeting.",
             "without any major objections.", "and filed the paperwork afterwards.",
             "while the weather stayed calm.", "according to the minutes."]
    sents, count = [], 0
    while count < words:
        s = f"{rnd.choice(subjects)} {rnd.choice(verbs)} {rnd.choice(objects)} {rnd.choice(tails)}"
        sents.append(s)
        count += len(s.split())
    sents.insert(len(sents) // 2, "The vault access code for Bayview Station is 4817.")
    sents.insert(len(sents) // 4, "The garage access code for Harbour Station is 2260.")
    return ("Read the following log and answer the question at the end.\n\n"
            + " ".join(sents)
            + "\n\nQuestion: What is the vault access code for Bayview Station? Answer with the code only.")


def get_prompt(q, args):
    if q.get("dynamic") == "long":
        return build_long_prompt(args.long_words)
    return q["prompt"]


def get_ctx(q, args):
    if q.get("dynamic") == "long":
        return max(args.num_ctx, int(args.long_words * 1.6) + 1500)
    return args.num_ctx


# -------------------------------------------------------------- ollama API
def post(host, path, payload, timeout):
    req = urllib.request.Request(host.rstrip("/") + path,
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def chat(args, model, prompt, num_ctx, think, judge=False):
    opts = {"num_ctx": num_ctx, "seed": args.seed, "num_predict": args.max_tokens}
    if judge:
        opts["temperature"] = 0
    elif args.temperature is not None:
        opts["temperature"] = args.temperature
    payload = {"model": model, "stream": False, "options": opts, "keep_alive": "10m",
               "messages": [{"role": "user", "content": prompt}]}
    if think is not None:
        payload["think"] = think
    t0 = time.time()
    try:
        d = post(args.host, "/api/chat", payload, args.timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        if think is not None and "think" in body.lower():
            payload.pop("think")
            d = post(args.host, "/api/chat", payload, args.timeout)
        else:
            raise RuntimeError(f"HTTP {e.code}: {body[:200]}")
    dt = time.time() - t0
    m = d.get("message", {})
    ec = d.get("eval_count") or 0
    ed = d.get("eval_duration") or 0
    return {"answer": strip_think(m.get("content", "")),
            "thinking": m.get("thinking", "") or "",
            "tokens": ec, "tok_s": (ec / (ed / 1e9)) if ed else 0.0, "seconds": dt}


def judge_answer(args, q, answer):
    question = q.get("short") or q["prompt"]
    prompt = ("You are grading an answer against a reference.\n\n"
              f"Question:\n{question}\n\nReference answer / criteria:\n{q['ref']}\n\n"
              f"Candidate answer:\n{answer[:4000]}\n\n"
              "Does the candidate's final answer agree with the reference? "
              "Reply with exactly one word: PASS or FAIL.")
    try:
        txt = chat(args, args.judge, prompt, 8192, False, judge=True)["answer"].upper()
    except Exception:
        return None
    passes = bool(re.search(r"\bPASS\b", txt))
    fails = bool(re.search(r"\bFAIL\b", txt))
    if passes and not fails:
        return True
    if fails and not passes:
        return False
    return None


# ----------------------------------------------------------------- reports
def build_md(args, results, qs, sys_info=None):
    models = []
    for r in results:
        if r["model"] not in models:
            models.append(r["model"])
    cats = []
    for q in qs:
        if q["cat"] not in cats:
            cats.append(q["cat"])

    sys_line = ""
    if sys_info:
        cpu = sys_info.get("cpu", "")
        gpu = sys_info.get("gpu", "")
        ram = sys_info.get("ram_total_gb")
        sys_line = f"Host: {cpu} | {gpu} | {ram} GB RAM | {sys_info.get('os', '')}\n"

    L = ["# Ollama model test", "",
         f"Run {datetime.now():%Y-%m-%d %H:%M} | num_ctx={args.num_ctx} | think={args.think} | max_tokens={args.max_tokens}",
         sys_line,
         "## Scores (auto-graded)", "",
         "| Model | Total | " + " | ".join(cats) + " | Avg tok/s | Avg s/question |",
         "|" + "---|" * (len(cats) + 4)]
    for m in models:
        rs = [r for r in results if r["model"] == m]
        graded = [r for r in rs if r["auto"] is not None]
        cells = []
        for c in cats:
            g = [r for r in graded if r["cat"] == c]
            cells.append(f"{sum(1 for r in g if r['auto'])}/{len(g)}" if g else "-")
        tps = [r["tok_s"] for r in rs if r["tok_s"] > 0]
        L.append(f"| {m} | {sum(1 for r in graded if r['auto'])}/{len(graded)} | "
                 + " | ".join(cells)
                 + f" | {(sum(tps) / len(tps)) if tps else 0:.1f} | {sum(r['seconds'] for r in rs) / len(rs):.1f} |")
    L += ["", "## Per question", "",
          "| # | Category | Question | " + " | ".join(models) + " |",
          "|" + "---|" * (len(models) + 3)]
    for q in qs:
        cells = []
        for m in models:
            r = next((x for x in results if x["model"] == m and x["qid"] == q["id"]), None)
            if not r:
                cells.append("-")
                continue
            c = fmt(r["auto"])
            if args.judge:
                c += f" / judge {fmt(r['judge'])}"
            cells.append(c)
        short = (q.get("short") or q["prompt"]).replace("\n", " ").replace("|", "/")[:60]
        L.append(f"| {q['id']} | {q['cat']} | {short} | " + " | ".join(cells) + " |")
    L += ["", "## Reference answers", ""]
    for q in qs:
        L.append(f"{q['id']}. {q['ref']}")
    return "\n".join(L) + "\n"


def write_outputs(args, results, qs, outdir, sys_info=None, update_dashboard=True):
    os.makedirs(outdir, exist_ok=True)
    run_id = os.path.basename(os.path.normpath(outdir))

    # Structured JSON v2 with system telemetry and benchmark parameters
    payload = {
        "version": 2,
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(),
        "system": sys_info if sys_info is not None else get_system_info(args.host),
        "settings": {
            "num_ctx": args.num_ctx,
            "think": args.think,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
            "seed": args.seed,
        },
        "models": list(dict.fromkeys(r["model"] for r in results)),
        "records": results,
    }

    with open(os.path.join(outdir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    cols = ["model", "qid", "category", "auto", "judge", "tokens", "tok_s",
            "seconds", "reference", "answer"]
    with open(os.path.join(outdir, "results.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in results:
            w.writerow([r["model"], r["qid"], r["cat"], fmt(r["auto"]), fmt(r["judge"]),
                        r["tokens"], f"{r['tok_s']:.1f}", f"{r['seconds']:.1f}",
                        r["ref"], r["answer"]])

    md = build_md(args, results, qs, sys_info)
    with open(os.path.join(outdir, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)

    # Auto-consolidate and update dashboard
    if update_dashboard:
        try:
            build_dashboard(bench_dir=os.path.dirname(os.path.abspath(outdir)))
        except Exception as err:
            print(f"[Dashboard update note] {err}")

    return md


# ------------------------------------------------------- dashboard builder
def build_dashboard_data(bench_dir="bench_results"):
    """Scan all runs in bench_results and compile unified dashboard dataset."""
    runs = []
    records = []
    labels = {q["id"]: (q.get("short") or q["prompt"]) for q in QUESTIONS}

    pattern = os.path.join(bench_dir, "*", "results.json")
    for filepath in sorted(glob.glob(pattern)):
        run_folder = os.path.basename(os.path.dirname(filepath))
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = json.load(f)
        except (OSError, ValueError):
            continue

        recs = content.get("records") if isinstance(content, dict) else content
        if not isinstance(recs, list) or not all(isinstance(r, dict) and isinstance(r.get("model"), str) for r in recs):
            continue

        metadata = content if isinstance(content, dict) else {}
        run_id = metadata.get("run_id") or run_folder
        models = metadata.get("models") or sorted({r["model"] for r in recs})
        runs.append({
            "id": run_id,
            "time": metadata.get("timestamp") or run_id,
            "models": models,
            "count": len(recs),
            "system": metadata.get("system"),
            "settings": metadata.get("settings"),
        })
        records.extend(dict(r, run=run_id) for r in recs)

    return {
        "runs": runs,
        "records": records,
        "labels": labels,
        "system": {},
        "updated_at": datetime.now().isoformat(),
    }


def build_dashboard(bench_dir="bench_results", out_html="index.html"):
    """Write the consolidated index and embed it in a standalone dashboard."""
    data = build_dashboard_data(bench_dir)
    if not data["records"]:
        return
    os.makedirs(bench_dir, exist_ok=True)

    # Save index.json for HTTP fetch
    with open(os.path.join(bench_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    template_candidates = [
        out_html,
        os.path.join(os.path.dirname(__file__), "index.html"),
    ]
    template_path = next((p for p in template_candidates if os.path.exists(p)), None)
    if not template_path:
        return

    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    json_str = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")

    # Safe string slicing replacement - avoids regex backslash/backreference corruption with LaTeX answers
    start_tag = '<script type="application/json" id="dashboard-data">'
    end_tag = '</script>'
    start_idx = html.find(start_tag)
    end_idx = html.find(end_tag, start_idx)
    if start_idx != -1 and end_idx != -1:
        new_html = html[:start_idx + len(start_tag)] + "\n" + json_str + "\n" + html[end_idx:]
    else:
        new_html = html

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(new_html)


# ----------------------------------------------------- live web server
class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, bench_dir="bench_results", **kwargs):
        self.bench_dir = bench_dir
        super().__init__(*args, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/results":
            data = build_dashboard_data(self.bench_dir)
            if not data["records"]:
                published = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")
                try:
                    with open(published, encoding="utf-8") as snapshot:
                        data = json.load(snapshot)
                except (OSError, ValueError):
                    pass
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        elif self.path == "/api/status":
            if BENCH_STATUS["running"] and BENCH_STATUS["start_time"]:
                BENCH_STATUS["elapsed_seconds"] = round(time.time() - BENCH_STATUS["start_time"], 1)
            payload = json.dumps(BENCH_STATUS).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        elif self.path in ("/", "/dashboard", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()


def serve_dashboard(port=8080, bench_dir="bench_results", open_browser=True):
    directory = os.path.dirname(os.path.abspath(__file__))
    build_dashboard(bench_dir, os.path.join(directory, "index.html"))
    handler = partial(DashboardHandler, bench_dir=os.path.abspath(bench_dir), directory=directory)
    server_address = ("127.0.0.1", port)
    try:
        httpd = http.server.ThreadingHTTPServer(server_address, handler)
    except OSError:
        server_address = ("127.0.0.1", port + 1)
        httpd = http.server.ThreadingHTTPServer(server_address, handler)
        port = port + 1

    url = f"http://localhost:{port}"
    print(f"\n=======================================================")
    print(f"🚀 Ollama Benchmark Live Dashboard running at: {url}")
    print(f"Press Ctrl+C to stop the dashboard server.")
    print(f"=======================================================\n")

    if open_browser:
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server.")
    finally:
        httpd.server_close()


# -------------------------------------------------------------------- run
def parse_ids(spec):
    ids = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            ids.update(range(int(a), int(b) + 1))
        else:
            ids.add(int(part))
    return ids


def run_bench(args, update_dashboard=True):
    selected = parse_ids(args.only) if args.only else None
    qs = [q for q in QUESTIONS if selected is None or q["id"] in selected]
    if not qs:
        print("No questions selected.")
        return 1
    try:
        with urllib.request.urlopen(args.host.rstrip("/") + "/api/tags", timeout=15) as response:
            tags = json.loads(response.read())
    except Exception as e:
        print(f"Cannot reach Ollama at {args.host}: {e}")
        return 1

    have = {m.get("name") for m in tags.get("models", [])}
    wanted = list(args.models) + ([args.judge] if args.judge else [])
    missing = [m for m in wanted if m not in have and f"{m}:latest" not in have]
    if missing:
        print("Not installed: " + ", ".join(missing))
        print("Run: " + "; ".join(f"ollama pull {m}" for m in missing))
        return 1

    sys_info = get_system_info(args.host)
    print("\n---------------- System Environment ----------------")
    print(f"CPU: {sys_info.get('cpu')} ({sys_info.get('cpu_cores')} cores)")
    print(f"GPU: {sys_info.get('gpu')} ({sys_info.get('gpu_vram_mb')} MB)")
    print(f"RAM: {sys_info.get('ram_total_gb')} GB")
    print(f"OS:  {sys_info.get('os')}")
    if sys_info.get("ollama_version"):
        print(f"Ollama: v{sys_info.get('ollama_version')}")
    print("----------------------------------------------------\n")

    outdir = args.out or os.path.join("bench_results", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(outdir, exist_ok=True)

    think = {"default": None, "on": True, "off": False}[args.think]
    results = []

    BENCH_STATUS["running"] = True
    BENCH_STATUS["start_time"] = time.time()
    BENCH_STATUS["total_questions"] = len(qs) * len(args.models)
    BENCH_STATUS["completed_evaluations"] = 0

    try:
        for model in args.models:
            BENCH_STATUS["model"] = model
            print(f"\n=== {model} ===")
            for q in qs:
                BENCH_STATUS["qid"] = q["id"]
                prompt, ctx = get_prompt(q, args), get_ctx(q, args)
                try:
                    r = chat(args, model, prompt, ctx, think)
                    auto = q["check"](r["answer"])
                except Exception as e:
                    r = {"answer": f"[ERROR] {e}", "thinking": "", "tokens": 0, "tok_s": 0.0, "seconds": 0.0}
                    auto = False

                jd = judge_answer(args, q, r["answer"]) if args.judge and not r["answer"].startswith("[ERROR]") else None
                rec = {
                    "model": model, "qid": q["id"], "cat": q["cat"], "auto": auto,
                    "judge": jd, "tokens": r["tokens"], "tok_s": r["tok_s"],
                    "seconds": r["seconds"], "ref": q["ref"], "answer": r["answer"],
                    "thinking_chars": len(r["thinking"])
                }
                results.append(rec)
                BENCH_STATUS["completed_evaluations"] += 1

                # Incremental live save to disk
                md = write_outputs(args, results, qs, outdir, sys_info, update_dashboard=update_dashboard)

                extra = f"  judge={fmt(jd)}" if args.judge else ""
                print(f"Q{q['id']:>2} {q['cat']:<13} {fmt(auto):<4} {r['tok_s']:6.1f} tok/s {r['seconds']:7.1f}s{extra}")
    finally:
        BENCH_STATUS["running"] = False
        BENCH_STATUS["elapsed_seconds"] = round(time.time() - BENCH_STATUS["start_time"], 1)

    print("\n" + md)
    print(f"Saved to {outdir}  (report.md, results.csv, results.json)")

    if args.dashboard:
        serve_dashboard(port=args.port, bench_dir=os.path.dirname(os.path.abspath(outdir)), open_browser=True)

    return 0


# ---------------------------------------------------------------- selftest
def run_selftest(parser):
    bad = 0
    print("Checker tests (good answer must pass, bad answer must fail):")
    for q in QUESTIONS:
        g, b = q["check"](q["good"]), q["check"](q["bad"])
        ok = (g is True and b is False)
        bad += (not ok)
        print(f"  Q{q['id']:>2} good={fmt(g):<4} bad={fmt(b):<4} {'ok' if ok else 'PROBLEM'}")

    answers = {}
    a_ns = parser.parse_args(["--long-words", "300"])
    for q in QUESTIONS:
        answers[get_prompt(q, a_ns)] = q["good"]

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/version":
                self._send({"version": "mock-0.5.0"})
            else:
                self._send({"models": [{"name": "mock:latest"}]})

        def do_POST(self):
            d = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            prompt = d["messages"][0]["content"]
            text = "PASS" if prompt.startswith("You are grading") else answers.get(prompt, "unknown")
            self._send({"message": {"role": "assistant", "content": text, "thinking": ""},
                        "eval_count": 50, "eval_duration": int(1e9)})

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as tmp:
        args = parser.parse_args(["--models", "mock", "--judge", "mock", "--long-words", "300",
                                  "--host", f"http://127.0.0.1:{srv.server_address[1]}",
                                  "--out", tmp])
        print("\nEnd-to-end run against a mock Ollama server:")
        try:
            rc = run_bench(args, update_dashboard=False)
        finally:
            srv.shutdown()
            srv.server_close()
        with open(os.path.join(tmp, "results.json"), encoding="utf-8") as f:
            res_data = json.load(f)
        res = res_data.get("records") if isinstance(res_data, dict) else res_data
        passed = sum(1 for r in res if r["auto"] is True)
        judged = sum(1 for r in res if r["judge"] is True)
        files = all(os.path.exists(os.path.join(tmp, n)) for n in ("report.md", "results.csv", "results.json"))
        print(f"\nmock run: auto {passed}/{len(res)}, judge {judged}/{len(res)}, files written: {files}")
        ok = (bad == 0 and rc == 0 and passed == len(res) and judged == len(res) and files)
        print("SELFTEST " + ("PASSED" if ok else "FAILED"))
        return 0 if ok else 1


def main(argv=None):
    global NO_EXEC
    p = argparse.ArgumentParser(description="Run tough questions against local Ollama models and score them.")
    p.add_argument("--models", nargs="+", help="model names, e.g. qwen3.5:9b gemma4:e4b")
    p.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    p.add_argument("--num-ctx", type=int, default=8192, help="context length (default 8192)")
    p.add_argument("--max-tokens", type=int, default=6000, help="max generated tokens per answer")
    p.add_argument("--think", choices=["default", "on", "off"], default="default")
    p.add_argument("--temperature", type=float, default=None, help="default: each model's own settings")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--only", default="", help='question ids, e.g. "1-10,16,17"')
    p.add_argument("--judge", default=None, help="optional model that grades answers against the reference")
    p.add_argument("--long-words", type=int, default=6000, help="size of the long-context question (words)")
    p.add_argument("--timeout", type=int, default=900, help="seconds per request")
    p.add_argument("--out", default=None, help="output folder")
    p.add_argument("--no-exec", action="store_true", help="do not run model-written code (Q11 is then not graded)")
    p.add_argument("--list", action="store_true", help="list the questions and exit")
    p.add_argument("--selftest", action="store_true", help="test the script itself and exit")
    p.add_argument("--build-dashboard", action="store_true", help="consolidate bench_results and rebuild index.html")
    p.add_argument("--serve", action="store_true", help="start live dashboard web server and open browser")
    p.add_argument("--dashboard", action="store_true", help="open dashboard web server after running benchmark")
    p.add_argument("--port", type=int, default=8080, help="dashboard web server port (default: 8080)")

    args = p.parse_args(argv)
    if "://" not in args.host:
        args.host = "http://" + args.host
    args.host = args.host.replace("0.0.0.0", "localhost")
    NO_EXEC = args.no_exec

    if args.selftest:
        return run_selftest(p)
    if args.list:
        for q in QUESTIONS:
            print(f"{q['id']:>2} [{q['cat']}] {(q.get('short') or q['prompt']).splitlines()[0][:90]}")
        return 0
    if args.build_dashboard:
        print("Consolidating benchmark results...")
        build_dashboard()
        print("Done! Updated index.html and bench_results/index.json.")
        return 0
    if args.serve:
        serve_dashboard(port=args.port, bench_dir="bench_results", open_browser=True)
        return 0
    if not args.models:
        p.error("--models is required (or use --serve, --build-dashboard, --list, --selftest)")
    return run_bench(args)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())

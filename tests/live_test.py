#!/usr/bin/env python3
"""Live smoke tests for the eurysx CLI: AGENTS.md -> "Live smoke tests" L1-L16.

Drives a real CLI process (the installed `eurysx` console script when present, otherwise
the source tree) against throwaway directories, then asserts on exit codes, stdout,
stderr, exported artifacts, and the SQLite store itself.

    python3 tests/live_test.py                     # every check
    python3 tests/live_test.py --list              # machine-readable inventory
    python3 tests/live_test.py --json              # machine-readable results
    python3 tests/live_test.py --only L5,L15       # subset
    python3 tests/live_test.py --keep --old-tree /path/to/0.1.4

Hermetic by construction: EURYSX_CONFIG_DIR, EURYSX_DATA_DIR, EURYSX_CACHE_DIR, and HOME
all point inside the sandbox, so nothing real is collected and no pricing source is
enabled (network stays unused). Report fixtures are re-dated relative to today, so no
check depends on the calendar matching the committed fixtures.

Exit status: 0 all checks passed, 1 at least one failed, 2 bad invocation. Skipped checks
are reported but never fail the run; a skip means the condition needs input the script
cannot invent (a real store, an older checkout, an Anthropic response file).
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "anthropic_usage"
USAGE_KIND = "anthropic-usage-report"
COST_KIND = "anthropic-cost-report"


class Skip(Exception):
    """A check whose precondition is unavailable in this environment."""


def report_paths(context):
    """Dates used by the synthetic reports: three closed days, all older than 3 days."""
    newest = context["today"] - timedelta(days=4)
    return [(newest - timedelta(days=offset)) for offset in (2, 1, 0)]


def make_report(context, kind, target, day_offset=0):
    """Copy a fixture and re-date its buckets, keeping every documented key name."""
    name = "usage-report.json" if kind == USAGE_KIND else "cost-report.json"
    payload = json.loads((FIXTURES / name).read_text())
    base = report_paths(context)[0] + timedelta(days=day_offset)
    for index, bucket in enumerate(payload["data"]):
        start = base + timedelta(days=index)
        bucket["time_range"] = {
            "start_date": f"{start:%Y-%m-%d}T00:00:00Z",
            "end_date": f"{start + timedelta(days=1):%Y-%m-%d}T00:00:00Z",
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload))
    return payload


def write_preferences(context, entries):
    """A preferences.jsonc with every supported agent plus the declared import entries."""
    agents = {agent: {} for agent in ("claude-code", "codex", "opencode", "pi")}
    config = {"schemaVersion": 3, "agents": agents}
    if entries is not None:
        config["aggregate_imports"] = entries
    (context["sandbox"] / "config" / "preferences.jsonc").write_text(json.dumps(config, indent=2))


def entry(kind, path, scope="acme-live"):
    return {"type": kind, "path": str(path), "scope": scope}


def set_data_dir(context, path):
    """Point both the child process environment and the context at one store directory."""
    context["data"] = Path(path)
    context["data"].mkdir(parents=True, exist_ok=True)
    context["env"]["EURYSX_DATA_DIR"] = str(context["data"])


def run(context, args, expect=0, cwd=None):
    """Invoke the CLI under test and return the CompletedProcess."""
    result = subprocess.run([*context["cli"], *args], cwd=cwd or REPO, env=context["env"],
                            capture_output=True, text=True, timeout=300)
    if expect is not None and result.returncode != expect:
        raise AssertionError(f"eurysx {' '.join(args)}: exit {result.returncode}, "
                             f"expected {expect}\n{result.stdout[-500:]}\n{result.stderr[-500:]}")
    return result


def open_store(context, path=None):
    return sqlite3.connect(str(path or context["data"] / "eurysx.db"))


def lane_rows(context):
    with open_store(context) as store:
        store.row_factory = sqlite3.Row
        return [dict(row) for row in store.execute("select * from aggregate_imports")]


def lane_totals_sql(context):
    with open_store(context) as store:
        row = store.execute(
            "select count(*) as rows, sum(input_uncached_tokens) as uncached,"
            " sum(input_cached_tokens) as cached, sum(cache_creation_tokens) as creation,"
            " sum(output_tokens) as output, sum(cost_usd) as cost"
            " from aggregate_imports").fetchone()
    return dict(zip(("rows", "uncached", "cached", "creation", "output", "cost"), row))


def json_report(context, args=(), out="report.json"):
    target = context["out"] / out
    if target.exists():
        target.unlink()
    run(context, ["report", *args, "--output", str(target)])
    return json.loads(target.read_text())


def terminal(context, args):
    return run(context, ["report", *args])


def fresh_imports(context, kinds=(USAGE_KIND, COST_KIND)):
    """Reset the store directory and land one report file per kind in the sandbox."""
    set_data_dir(context, context["sandbox"] / "data")
    shutil.rmtree(context["data"], ignore_errors=True)
    set_data_dir(context, context["sandbox"] / "data")
    shutil.rmtree(context["imports"], ignore_errors=True)
    entries = []
    for kind in kinds:
        path = context["imports"] / f"acme-{'usage' if kind == USAGE_KIND else 'cost'}.json"
        make_report(context, kind, path)
        entries.append(entry(kind, path))
    write_preferences(context, entries)
    return entries


# --------------------------------------------------------------------------- checks

def check_L1(context):
    """Local store, no imports declared: nothing about the lane leaks into the report.

    Uses `report` rather than the bare analyze command because the sandbox HOME holds no
    harness history, so `detect_agents()` is empty by design.
    """
    if not context["baseline"]:
        raise Skip("no local eurysx.db to copy")
    target = context["sandbox"] / "baseline"
    shutil.rmtree(target, ignore_errors=True)
    set_data_dir(context, target)
    shutil.copyfile(context["baseline"], target / "eurysx.db")
    write_preferences(context, None)
    result = terminal(context, ("--days", "30"))
    out = json_report(context, ("--days", "30"), out="l1.json")
    assert "PROVIDER-REPORTED AGGREGATES" not in result.stdout, "lane rendered without imports"
    assert out["aggregate_imports"]["totals"]["rows"] == 0, out["aggregate_imports"]["totals"]
    assert out["aggregate_imports"]["totals"]["reported_cost_usd"] is None, "null cost expected"
    assert out["schema_version"] == 2, out["schema_version"]
    return "no lane block, JSON lane rows 0 with null cost"


def check_L2(context):
    """doctor states the absence explicitly."""
    write_preferences(context, None)
    result = run(context, ["doctor"])
    assert "No aggregate imports configured." in result.stdout, result.stdout[-400:]
    return "doctor: No aggregate imports configured."


def check_L3(context):
    """First collect ingests one source per declared glob."""
    fresh_imports(context)
    result = run(context, ["collect"])
    lines = [line for line in result.stdout.splitlines() if "aggregate row(s)" in line]
    assert len(lines) == 2, result.stdout
    assert lane_totals_sql(context)["rows"] == 8, "expected 8 deduped buckets"
    return "2 sources ingested, 8 rows stored"


def check_L4(context):
    """An untouched fingerprint skips re-parsing entirely."""
    before = {row["ingested_at"] for row in lane_rows(context)}
    result = run(context, ["collect"])
    assert result.stdout.count("unchanged aggregate import.") == 2, result.stdout
    assert {row["ingested_at"] for row in lane_rows(context)} == before, "rows were rewritten"
    return "both entries reported unchanged, ingested_at stable"


def check_L5(context):
    """Lane arithmetic: JSON totals equal the SQL sums, usage rows stay cost-free."""
    totals = json_report(context, ("--days", "30"))["aggregate_imports"]["totals"]
    sql = lane_totals_sql(context)
    expected = {"rows": sql["rows"], "input_uncached_tokens": sql["uncached"] or 0,
                "input_cached_tokens": sql["cached"] or 0,
                "cache_creation_tokens": sql["creation"] or 0,
                "output_tokens": sql["output"] or 0}
    for key, value in expected.items():
        assert totals[key] == value, (key, totals[key], value)
    assert Decimal(str(totals["reported_cost_usd"])) == Decimal(str(sql["cost"])), (
        totals["reported_cost_usd"], sql["cost"])
    kinds = {}
    for row in lane_rows(context):
        kinds.setdefault(row["source_kind"], []).append(row["cost_usd"])
    assert all(value is None for value in kinds[USAGE_KIND]), "usage rows must not carry cost"
    assert all(value is not None for value in kinds[COST_KIND]), "cost rows must carry cost"
    return f"rows {totals['rows']}, usd {totals['reported_cost_usd']} match SQL"


def check_L6(context):
    """Selectors: billing-mode and agent never filter the lane, --model does by design."""
    unfiltered = json_report(context, ("--days", "30"), out="l6-all.json")["aggregate_imports"]
    note = terminal(context, ("--days", "30", "--billing-mode", "subscription"))
    assert "filters local usage only; this lane is unfiltered." in note.stdout, note.stdout
    by_mode = json_report(context, ("--days", "30", "--billing-mode", "subscription"),
                          out="l6-mode.json")["aggregate_imports"]
    assert by_mode["totals"] == unfiltered["totals"], (by_mode["totals"], unfiltered["totals"])
    narrow = json_report(context, ("--days", "30", "--model", "claude-opus-4-1"),
                         out="l6-model.json")["aggregate_imports"]
    assert 0 < narrow["totals"]["rows"] < unfiltered["totals"]["rows"], narrow["totals"]
    return "lane cost unchanged by --billing-mode, narrowed by --model"


def check_L7(context):
    """All four exporters carry the lane; HTML keeps it index-only."""
    out = context["out"]
    run(context, ["report", "--days", "30", "--format", "csv", "--output", str(out / "l7.csv")])
    run(context, ["report", "--days", "30", "--format", "markdown", "--output", str(out / "l7.md")])
    site = out / "l7.html"
    shutil.rmtree(site, ignore_errors=True)
    run(context, ["report", "--days", "30", "--format", "html", "--output", str(site)])
    csv_text = (out / "l7.csv").read_text()
    markdown = (out / "l7.md").read_text()
    pages = sorted(path.name for path in site.iterdir())
    index = (site / "index.html").read_text()
    assert "reported_cost_usd" in csv_text.splitlines()[0], csv_text.splitlines()[0]
    assert "reported_aggregate" in csv_text and ",N/A,N/A,N/A," in csv_text, csv_text[:200]
    assert "## Provider-Reported Aggregates" in markdown, markdown[:200]
    assert pages == ["index.html"], pages
    assert "Provider-Reported Aggregates" in index, "lane missing from index.html"
    return "csv column + rows, markdown section, html index-only"


def check_L8(context):
    """A corrupted file keeps last-good rows and records the failure."""
    entries = fresh_imports(context)
    run(context, ["collect"])
    keep = lane_rows(context)
    target = Path(entries[0]["path"])
    original = target.read_text()
    target.write_text("{oops")
    result = run(context, ["collect"])
    try:
        assert "aggregate import failed" in result.stdout, result.stdout
        assert "last-good rows retained" in result.stdout, result.stdout
        assert lane_totals_sql(context)["rows"] == len(keep), "rows were dropped"
        doctor = run(context, ["doctor"]).stdout
        assert "last error:" in doctor, doctor[-500:]
    finally:
        target.write_text(original)
    return "failure warned, rows retained, doctor shows last error"


def check_L9(context):
    """A glob matching nothing warns at report time and reads as not collected."""
    entries = fresh_imports(context)
    run(context, ["collect"])
    ghost = entry(USAGE_KIND, context["imports"] / "missing-*.json", scope="ghost-live")
    write_preferences(context, entries + [ghost])
    result = run(context, ["report", "--days", "30"])
    doctor = run(context, ["doctor"]).stdout
    assert "has no matching files" in result.stderr, result.stderr[-400:]
    assert "ghost-live" in doctor and "not collected" in doctor, doctor[-500:]
    assert "rows:" in doctor, "healthy entries must still be listed"
    return "report warned, doctor: 0 file(s) matched; not collected"


def check_L10(context):
    """Invalid entries are dropped with a warning; valid ones still run."""
    fresh_imports(context)
    run(context, ["collect"])
    write_preferences(context, [
        entry(USAGE_KIND, context["imports"] / "acme-usage.json"),
        {"type": "openai-cost-report", "path": str(context["imports"] / "acme-cost.json"),
         "scope": "bad-type"},
        {"type": USAGE_KIND, "path": 5, "scope": ""},
    ])
    result = run(context, ["collect"])
    expect = ("aggregate import entry ignored: unsupported type openai-cost-report",
              "requires a non-empty path", "acme-live")
    for text in expect[:2]:
        assert text in result.stderr, f"{text!r} absent from stderr: {result.stderr[-200:]!r}"
    assert expect[2] in result.stdout, f"valid entry unprocessed: {result.stdout[-200:]!r}"
    assert lane_totals_sql(context)["rows"] == 8, "valid entry lost its rows"
    return "2 invalid entries ignored, valid entry still processed, exit 0"


def check_L11(context):
    """One glob, two files, disagreeing bucket: first path wins with a counted warning."""
    set_data_dir(context, context["sandbox"] / "data")
    shutil.rmtree(context["data"], ignore_errors=True)
    set_data_dir(context, context["sandbox"] / "data")
    shutil.rmtree(context["imports"], ignore_errors=True)
    winner = context["imports"] / "acme-usage-0.json"
    loser = context["imports"] / "acme-usage-1.json"
    make_report(context, USAGE_KIND, winner)
    payload = json.loads(winner.read_text())
    payload["data"][0]["results"][0]["iterations"]["input_tokens"] = 999999
    loser.write_text(json.dumps(payload))
    write_preferences(context, [entry(USAGE_KIND, context["imports"] / "acme-usage-*.json")])
    result = run(context, ["collect"])
    assert "1 duplicate bucket(s) with disagreeing values" in result.stderr, result.stderr[-400:]
    assert "first file's value was kept" in result.stderr, result.stderr[-400:]
    rows = lane_rows(context)
    assert len(rows) == 4, [row["date"] for row in rows]
    assert {row["source_file"] for row in rows} == {str(winner)}, "wrong file won the bucket"
    assert all(row["input_uncached_tokens"] != 999999 for row in rows), "loser value was stored"
    return "counted warning, deduped to 4 rows, path-order-first file kept"


def check_L12(context):
    """Staleness is disclosed per source."""
    fresh_imports(context)
    run(context, ["collect"])
    result = run(context, ["report", "--days", "30"])
    stale = [line for line in result.stderr.splitlines() if "is stale" in line]
    assert len(stale) == 2, result.stderr[-400:]
    assert "newest complete bucket is" in stale[0], stale[0]
    return "2 staleness warnings (once per source)"


def check_L13(context):
    """A version-1 store opens, gains the new table, and keeps every event row."""
    if not context["baseline"]:
        raise Skip("no local eurysx.db to downgrade")
    target = context["sandbox"] / "migration" / "eurysx.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(context["baseline"], target)
    with sqlite3.connect(str(target)) as store:
        before = store.execute("select count(*) from events").fetchone()[0]
        store.execute("drop table if exists aggregate_imports")
        store.execute("pragma user_version=1")
        store.commit()
    set_data_dir(context, target.parent)
    result = run(context, ["report", "--days", "30"])
    with sqlite3.connect(str(target)) as store:
        version = store.execute("pragma user_version").fetchone()[0]
        tables = {row[0] for row in store.execute("select name from sqlite_master where type='table'")}
        after = store.execute("select count(*) from events").fetchone()[0]
    assert before > 0, "baseline store has no events to protect"
    assert version == 2, f"user_version {version}"
    assert "aggregate_imports" in tables, sorted(tables)
    assert after == before, f"events {before} -> {after}"
    assert "No stored usage data found" not in result.stdout, result.stdout
    return f"v1 -> v2 in place, {after} events intact"


def check_L14(context):
    """CLI contract: usage errors exit 2, an unknown --output extension writes JSON."""
    fresh_imports(context)
    run(context, ["collect"])
    run(context, ["report", "--to", "2026-01-02"], expect=2)
    run(context, ["report", "--format", "csv"], expect=2)
    run(context, ["--bogus"], expect=2)
    target = context["out"] / "l14.unknown"
    run(context, ["report", "--days", "30", "--output", str(target)])
    assert json.loads(target.read_text())["schema_version"] == 2, "unknown ext must write JSON"
    return "3 usage errors exit 2; unknown extension writes JSON"


def check_L15(context):
    """An imports-only store still reports the lane, in every format."""
    fresh_imports(context)
    run(context, ["collect"])
    result = run(context, ["report", "--days", "30"])
    assert "No local harness usage stored" in result.stdout, result.stdout[:400]
    assert "PROVIDER-REPORTED AGGREGATES" in result.stdout, result.stdout[:400]
    assert "No stored usage data found" not in result.stdout, result.stdout[:400]
    payload = json_report(context, ("--days", "30"), out="l15.json")
    assert payload["agent_stats"] == {}, sorted(payload["agent_stats"])
    site = context["out"] / "l15.html"
    shutil.rmtree(site, ignore_errors=True)
    run(context, ["report", "--days", "30", "--format", "html", "--output", str(site)])
    pages = sorted(path.name for path in site.iterdir())
    assert pages == ["index.html"], pages
    assert "No local harness usage" in (site / "index.html").read_text()
    return "hint + lane in terminal/JSON, html index-only with no token leader"


def check_L16(context):
    """No harness history: collect refreshes imports instead of stopping."""
    fresh_imports(context)
    result = run(context, ["collect"])
    assert "No local harnesses detected" in result.stdout, result.stdout[:400]
    assert "No agents detected" not in result.stdout, result.stdout[:400]
    assert lane_totals_sql(context)["rows"] == 8, "imports were not ingested"
    write_preferences(context, None)
    fresh_imports(context, kinds=())
    empty = run(context, ["collect"])
    assert "No agents detected." in empty.stdout, empty.stdout[:400]
    return "imports ingested without harnesses; empty config still refuses politely"


def check_L17(context):
    """Local lanes are byte-identical to the previous release on the same store copy."""
    if not (context["old_tree"] and context["baseline"]):
        raise Skip("needs --old-tree plus a local eurysx.db")
    sandbox = context["sandbox"] / "compare"
    for name in ("old", "new"):
        (sandbox / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(context["baseline"], sandbox / name / "eurysx.db")
        with sqlite3.connect(str(sandbox / name / "eurysx.db")) as store:
            # Present both runs with a version-1 store: 0.1.4 rejects version 2 outright,
            # and the new release migrates it in place, which is the path under test.
            store.execute("drop table if exists aggregate_imports")
            store.execute("pragma user_version=1")
            store.commit()
    old_cli = [sys.executable, "-m", "eurysx.cli"]
    old_env = dict(context["env"], PYTHONPATH=str(context["old_tree"] / "src"))
    old_env["EURYSX_DATA_DIR"] = str(sandbox / "old")
    old_env["EURYSX_CACHE_DIR"] = str(sandbox / "old-cache")
    (sandbox / "old-cache").mkdir(parents=True, exist_ok=True)
    shutil.copytree(context["cache"], sandbox / "old-cache", dirs_exist_ok=True)
    run({"cli": old_cli, "env": old_env}, ["report", "--days", "30", "--output",
        str(sandbox / "old.json")], cwd=context["old_tree"])
    new_env = dict(context["env"], EURYSX_DATA_DIR=str(sandbox / "new"),
                   EURYSX_CACHE_DIR=str(sandbox / "new-cache"))
    (sandbox / "new-cache").mkdir(parents=True, exist_ok=True)
    shutil.copytree(context["cache"], sandbox / "new-cache", dirs_exist_ok=True)
    run({"cli": context["cli"], "env": new_env}, ["report", "--days", "30", "--output",
        str(sandbox / "new.json")])
    old = json.loads((sandbox / "old.json").read_text())
    new = json.loads((sandbox / "new.json").read_text())
    lane, version = new.pop("aggregate_imports"), new.pop("schema_version")
    old.pop("schema_version")
    assert version == 2, version
    assert lane["totals"]["rows"] == 0, "baseline copy must carry no imports"
    assert old == new, "local lanes diverge from the previous release"
    return "every JSON key except schema_version/aggregate_imports identical"


CHECKS = [(name, function.__doc__.strip()) for name, function in [
    ("L1", check_L1), ("L2", check_L2), ("L3", check_L3), ("L4", check_L4), ("L5", check_L5),
    ("L6", check_L6), ("L7", check_L7), ("L8", check_L8), ("L9", check_L9), ("L10", check_L10),
    ("L11", check_L11), ("L12", check_L12), ("L13", check_L13), ("L14", check_L14),
    ("L15", check_L15), ("L16", check_L16), ("L17", check_L17),
]]


def resolve_cli(override):
    if override:
        return override.split() if isinstance(override, str) else list(override)
    found = shutil.which("eurysx")
    if found:
        return [found]
    return [sys.executable, "-m", "eurysx.cli"]


def build_context(options):
    sandbox = Path(tempfile.mkdtemp(prefix="eurysx-live-")) if not options.keep_dir else Path(options.keep_dir)
    sandbox.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    paths = {name: sandbox / name for name in ("config", "data", "cache", "imports", "out", "home")}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    cli = resolve_cli(options.eurysx)
    env.update({
        "EURYSX_CONFIG_DIR": str(paths["config"]),
        "EURYSX_DATA_DIR": str(paths["data"]),
        "EURYSX_CACHE_DIR": str(paths["cache"]),
        "HOME": str(paths["home"]),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if "eurysx.cli" in cli[1:]:  # module form: make this checkout importable
        env["PYTHONPATH"] = str(REPO / "src")
    baseline = None
    if options.store:
        candidate = Path(options.store).expanduser()
        if candidate.exists():
            baseline = candidate
    if options.seed_pricing and (REPO / "config" / "pricing.jsonc").exists():
        shutil.copyfile(REPO / "config" / "pricing.jsonc", paths["config"] / "pricing.jsonc")
        for cache in (REPO / "cache").glob("pricing-*.json"):
            shutil.copyfile(cache, paths["cache"] / cache.name)
    old_tree = Path(options.old_tree).expanduser().resolve() if options.old_tree else None
    return {"sandbox": sandbox, "cli": cli, "env": env,
            "today": date.today(), "baseline": baseline, "old_tree": old_tree,
            "verbose": options.verbose, **paths}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="print the check inventory as JSON and exit")
    parser.add_argument("--only", help="comma-separated check ids, e.g. L5,L15")
    parser.add_argument("--json", action="store_true", help="emit results as JSON")
    parser.add_argument("--eurysx", help="CLI command to test (default: installed eurysx, else the source tree)")
    parser.add_argument("--store", default=str(REPO / "data" / "eurysx.db"),
                        help="local store to copy for baseline/migration checks ('' to skip them)")
    parser.add_argument("--old-tree", help="checkout of the previous release for the byte-compare check")
    parser.add_argument("--seed-pricing", action="store_true",
                        help="copy this checkout's pricing config and cache into the sandbox")
    parser.add_argument("--keep-dir", help="use a fixed sandbox directory instead of a fresh one")
    parser.add_argument("--verbose", action="store_true", help="echo each command before running it")
    options = parser.parse_args(argv)

    if options.list:
        print(json.dumps([{"id": name, "doc": doc} for name, doc in CHECKS], indent=2))
        return 0

    context = build_context(options)
    wanted = None if not options.only else {item.strip().upper() for item in options.only.split(",")}
    results = []
    started = time.time()
    for name, doc in CHECKS:
        if wanted and name not in wanted:
            continue
        try:
            detail = getattr(sys.modules[__name__], f"check_{name}")(context)
            results.append({"id": name, "status": "pass", "detail": detail or ""})
        except Skip as exc:
            results.append({"id": name, "status": "skip", "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001 - report and keep going
            results.append({"id": name, "status": "fail",
                            "detail": f"{type(exc).__name__}: {exc}".splitlines()[0][:300]})
        if options.json:
            continue
        outcome = results[-1]
        print(f"{outcome['id']:4} {outcome['status'].upper():5} {doc.split('.')[0]}"
              f" :: {outcome['detail'][:120]}", flush=True)
    failed = [item for item in results if item["status"] == "fail"]
    summary = {"cli": " ".join(context["cli"]), "sandbox": str(context["sandbox"]),
               "checks": len(results), "failed": len(failed),
               "elapsed_seconds": round(time.time() - started, 1), "results": results}
    if options.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"\n{len(results) - len(failed)}/{len(results)} passed in {summary['elapsed_seconds']}s"
              f" using {' '.join(context['cli'])}\nsandbox: {context['sandbox']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

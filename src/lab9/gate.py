"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS]
    for name, rep in (("PR", pr), ("baseline", base)):
        problems += [f"{name} metric {k} is {v!r} (NaN/inf never compares as a failure)"
                     for k, v in (rep.get("metrics") or {}).items() if _non_finite(v)]
    rows, man = pr.get("rows") or [], pr.get("manifest") or {}
    want = int(man.get("items") or 0) * len(man.get("seeds") or [])
    if len(rows) != want:
        problems.append(f"PR has {len(rows)} rows, manifest says {man.get('items')} items x "
                        f"{len(man.get('seeds') or [])} seeds = {want}")
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    dupes = sorted({k for k in keys if keys.count(k) > 1}, key=str)
    if dupes:
        problems.append(f"PR has duplicated (item, seed) rows: {dupes[:5]}")
    base_rows = base.get("rows") or []
    pr_set, base_set = set(keys), {(r.get("item_id"), r.get("seed")) for r in base_rows}
    if pr_set != base_set:
        problems.append(f"(item, seed) set differs from the baseline: {len(base_set - pr_set)} missing, "
                        f"{len(pr_set - base_set)} extra (e.g. {sorted(base_set ^ pr_set, key=str)[:3]})")
    if (man.get("items"), list(man.get("seeds") or [])) != (base.get("manifest", {}).get("items"),
                                                            list(base.get("manifest", {}).get("seeds") or [])):
        problems.append("PR manifest (items, seeds) differs from the baseline's")
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s), first: {pr['evaluator_failures'][0]}")
    for name, rep in (("PR", pr), ("baseline", base)):
        for m in graded:
            bad = [f"{r.get('item_id')}@{r.get('seed')}" for r in rep.get("rows") or [] if r.get(m) is None]
            if bad:
                problems.append(f"{name} metric {m} has no value on {len(bad)} row(s) ({', '.join(bad[:3])}): "
                                f"a crashed grader must not drop out of the mean")
    problems += _recomputed_differs(pr, spec)
    base_tags = {r.get("item_id"): sorted(r.get("slices") or []) for r in base_rows}
    relabelled = sorted({r.get("item_id") for r in rows if r.get("item_id") in base_tags
                         and sorted(r.get("slices") or []) != base_tags[r.get("item_id")]})
    if relabelled:
        problems.append(f"slice tags differ from the baseline on {relabelled[:5]}")
    if base_spec is not None:
        problems += ratchet(spec, base_spec)
    return problems


def _non_finite(v) -> bool:
    return isinstance(v, float) and not math.isfinite(v)


def _recomputed_differs(pr: dict, spec: dict) -> list[str]:
    """Metrics must be what the rows say. A hand-edited number (or rows edited without re-aggregating)
    is caught by recomputing the aggregate from the rows with the harness's own code."""
    rows = pr.get("rows") or []
    if not rows:
        return ["PR has no rows"]
    ids = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS and all(m["id"] in r for r in rows)]
    try:
        again = aggregate([{"cost": 0.0, **r} for r in rows], ids, k_values=spec.get("pass_k", []))
    except Exception as e:  # noqa: BLE001 -- malformed rows are themselves an integrity problem
        return [f"cannot recompute metrics from rows: {e!r}"]
    out = []
    spec_ids = {m["id"] for m in spec.get("metrics", [])}
    for k, v in again.items():
        got = (pr.get("metrics") or {}).get(k)
        if isinstance(v, (int, float)) and isinstance(got, (int, float)):
            if not _non_finite(got) and abs(float(v) - float(got)) > 1e-6:
                out.append(f"metric {k}={got} but the rows give {v}")
        elif (v is None) != (got is None) and k in spec_ids:
            out.append(f"metric {k}={got!r} but the rows give {v!r}")
    return out


# Direction in which each threshold key is allowed to move (+1 = up only, -1 = down only).
_STRICTER = {"max": -1, "max_increase": -1, "min": +1}


def ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Same eval set => thresholds may only get stricter. Renaming eval_set_version while still
    pointing at the same eval-set file does not make it a new eval set."""
    if (spec.get("eval_set_version") != base_spec.get("eval_set_version")
            and spec.get("eval_set") != base_spec.get("eval_set")):
        return []          # genuinely new eval set: new baseline, thresholds start over
    out: list[str] = []
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        out.append(f"RATCHET eval_set_version {base_spec.get('eval_set_version')!r} -> "
                   f"{spec.get('eval_set_version')!r} but eval_set is still {spec.get('eval_set')}")
    if list(spec.get("seeds") or []) != list(base_spec.get("seeds") or []):
        out.append(f"RATCHET seeds changed {base_spec.get('seeds')} -> {spec.get('seeds')}")
    new = {m["id"]: m for m in spec.get("metrics", [])}
    for m in base_spec.get("metrics", []):
        cur = new.get(m["id"])
        out += _looser(f"metric {m['id']}", m, cur, m.get("threshold") or {}, (cur or {}).get("threshold") or {})
    new_sl = {(x.get("name"), x.get("metric")): x for x in spec.get("slices") or []}
    for x in base_spec.get("slices") or []:
        cur = new_sl.get((x.get("name"), x.get("metric")))
        out += _looser(f"slice {x.get('name')}/{x.get('metric')}", x, cur, x, cur or {})
    return out


def _looser(label: str, old: dict, new: dict | None, th_old: dict, th_new: dict) -> list[str]:
    if new is None:
        return [f"RATCHET {label} removed"]
    out = []
    if old.get("blocking") and not new.get("blocking"):
        out.append(f"RATCHET {label} no longer blocking")
    for key, sign in _STRICTER.items():
        if key not in th_old:
            continue
        if th_new.get(key) is None:
            out.append(f"RATCHET {label} dropped {key}")
        elif (float(th_new[key]) - float(th_old[key])) * sign < 0:
            out.append(f"RATCHET {label} {key} loosened {th_old[key]} -> {th_new[key]}")
    if th_old.get("no_significant_drop") and not th_new.get("no_significant_drop"):
        out.append(f"RATCHET {label} dropped no_significant_drop")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", "", None
        if not a:
            verdict = "missing"          # an empty slice proves nothing; never pass it silently
        else:
            val = round(sum(a.values()) / len(a), 4)
            if ("min" in sl and val < sl["min"]) or ("max" in sl and val > sl["max"]):
                verdict = "fail"
            keys = sorted(set(a) & set(b))
            if sl.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                                   confidence=bs.get("confidence", 0.95))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        lines.append(f"  {sid:<34} {val!s:<8} {json.dumps(th):<40} {note:<28} {label} ({len(a)} items)")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                               confidence=bs.get("confidence", 0.95))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

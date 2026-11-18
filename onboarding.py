#!/usr/bin/env python3
"""SaaS onboarding automation: go-live readiness report.

Reads a customer onboarding plan (JSON) and reports what an implementation
engineer needs before a go-live: which milestones are done, which tasks are
blocked or overdue, what is still missing for go-live, and a short list of
findings to compare against a manual review.

Standard library only. Exit code 1 when NOT READY for go-live, 0 when ready.

Usage:
    python3 onboarding.py sample_onboarding.json [--as-of YYYY-MM-DD]
                                                 [--json] [--html]
                                                 [--output report.md]
"""

import argparse
import html
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


MILESTONE_STATUSES = ("done", "in_progress", "not_started", "blocked")
CHECK_STATUSES = ("pass", "fail", "not_run")


def parse_date(value):
    """Parse a YYYY-MM-DD string, returning a date or None."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass
class Report:
    customer: str
    plan: str
    target_go_live: str
    as_of: date
    milestone_counts: dict
    total_milestones: int
    progress_pct: int
    overdue: list  # [(name, due date, days overdue)]
    blocked_tasks: list  # task dicts with status == "blocked"
    unfinished_milestones: list  # milestone names with status != "done"
    failing_checks: list  # check names with status == "fail"
    ready: bool
    findings: list = field(default_factory=list)
    milestones: list = field(default_factory=list)  # [{name, status, due, overdue_days}]


def analyze(plan, as_of):
    """Build the readiness report for one onboarding plan."""
    milestones = plan.get("milestones", [])
    tasks = plan.get("tasks", [])
    checks = plan.get("checks", [])

    counts = {status: 0 for status in MILESTONE_STATUSES}
    for milestone in milestones:
        status = milestone.get("status", "not_started")
        counts[status if status in counts else "not_started"] += 1

    total = len(milestones)
    done = counts["done"]
    progress_pct = round(100 * done / total) if total else 100

    # Overdue: still open and past due. Blocked milestones are reported
    # under blocked tasks instead, so they are not double-counted here.
    overdue = []
    for milestone in milestones:
        if milestone.get("status") not in ("in_progress", "not_started"):
            continue
        due = parse_date(milestone.get("due"))
        if due and due < as_of:
            overdue.append((milestone.get("name", "Unnamed milestone"), due, (as_of - due).days))

    blocked_tasks = [t for t in tasks if t.get("status") == "blocked"]
    unfinished = [
        m.get("name", "Unnamed milestone")
        for m in milestones
        if m.get("status") != "done"
    ]
    failing = [
        c.get("name", "Unnamed check")
        for c in checks
        if c.get("status") == "fail"
    ]

    ready = not unfinished and not failing and not blocked_tasks
    # Milestone rows for the HTML report: status, due date, overdue days.
    overdue_by_name = {name: days for name, _due, days in overdue}
    milestone_rows = []
    for milestone in milestones:
        due = parse_date(milestone.get("due"))
        milestone_rows.append({
            "name": milestone.get("name", "Unnamed milestone"),
            "status": milestone.get("status", "not_started"),
            "due": due.isoformat() if due else "",
            "overdue_days": overdue_by_name.get(milestone.get("name", "")),
        })
    report = Report(
        customer=plan.get("customer", "Unnamed customer"),
        plan=plan.get("plan", ""),
        target_go_live=plan.get("target_go_live", ""),
        as_of=as_of,
        milestone_counts=counts,
        total_milestones=total,
        progress_pct=progress_pct,
        overdue=overdue,
        blocked_tasks=blocked_tasks,
        unfinished_milestones=unfinished,
        failing_checks=failing,
        ready=ready,
        milestones=milestone_rows,
    )
    report.findings = build_findings(report)
    return report


def build_findings(report):
    """Short findings, written for comparison against a manual review."""
    findings = []
    for task in report.blocked_tasks:
        milestone = task.get("milestone", "a milestone")
        reason = task.get("blocked_by", "no reason given")
        findings.append(
            f"{milestone} is blocked on the customer side ({reason}) "
            "and gates go-live."
        )
    for name, _due, days in report.overdue:
        word = "day" if days == 1 else "days"
        findings.append(
            f"{name} is overdue by {days} {word}; confirm the work "
            "finished before re-running the related checks."
        )
    if report.failing_checks:
        names = "; ".join(report.failing_checks)
        word = "check" if len(report.failing_checks) == 1 else "checks"
        findings.append(f"{len(report.failing_checks)} failing {word}: {names}.")
    if report.ready:
        findings.append(
            "Nothing blocking go-live — all milestones done and all checks passing."
        )
    return findings


def format_text(report):
    """Plain-text report, the default output."""
    lines = []
    header = f"Onboarding: {report.customer}"
    if report.plan:
        header += f" ({report.plan})"
    if report.target_go_live:
        header += f" — target go-live {report.target_go_live}"
    lines.append(header)
    lines.append("=" * len(header))

    c = report.milestone_counts
    lines.append(
        f"Milestones: {c['done']} done, {c['in_progress']} in progress, "
        f"{c['not_started']} not started, {c['blocked']} blocked "
        f"of {report.total_milestones}"
    )
    lines.append(f"Progress: {report.progress_pct}%")
    lines.append("")

    if report.overdue:
        lines.append("Overdue milestones")
        lines.append("-" * 60)
        for name, due, days in report.overdue:
            word = "day" if days == 1 else "days"
            lines.append(f"  {name} — due {due.isoformat()} ({days} {word} overdue)")
        lines.append("")

    if report.blocked_tasks:
        lines.append("Blocked tasks")
        lines.append("-" * 60)
        for task in report.blocked_tasks:
            owner = task.get("owner", "unassigned")
            lines.append(f"  {task.get('name', 'Unnamed task')} — owner: {owner}")
            if task.get("blocked_by"):
                lines.append(f"    Blocked by: {task['blocked_by']}")
        lines.append("")

    lines.append(f"Go-live readiness: {'READY' if report.ready else 'NOT READY'}")
    lines.append("-" * 60)
    if report.ready:
        lines.append("  - All milestones done, all checks passing.")
    else:
        for name in report.unfinished_milestones:
            lines.append(f"  - Milestone not done: {name}")
        for name in report.failing_checks:
            lines.append(f"  - Failing check: {name}")
        for task in report.blocked_tasks:
            lines.append(f"  - Blocked task: {task.get('name', 'Unnamed task')}")
    lines.append("")

    lines.append("Findings to compare against your own review")
    lines.append("-" * 60)
    for finding in report.findings:
        lines.append(f"  - {finding}")
    return "\n".join(lines) + "\n"


STATUS_LABELS = {
    "done": "Done",
    "in_progress": "In progress",
    "not_started": "Not started",
    "blocked": "Blocked",
}


def format_html(report):
    """Visual dashboard-style report for non-technical readers (CSMs, executives).

    A single self-contained HTML file: inline CSS, no JavaScript, no
    external resources, so it can be opened from disk or attached to an
    email. All plan-supplied text is HTML-escaped.
    """
    esc = html.escape
    c = report.milestone_counts
    total = report.total_milestones or 1

    def pct(n):
        return round(100 * n / total)

    bar = "".join(
        f'<span style="width:{pct(c[s])}%;background:{color}"></span>'
        for s, color in (
            ("done", "#2da44e"),
            ("in_progress", "#0969da"),
            ("not_started", "#d0d7de"),
            ("blocked", "#cf222e"),
        )
        if c[s]
    )
    rows = []
    for m in report.milestones:
        status = m["status"] if m["status"] in STATUS_LABELS else "not_started"
        color = {
            "done": "#2da44e",
            "in_progress": "#0969da",
            "not_started": "#6e7781",
            "blocked": "#cf222e",
        }[status]
        note = ""
        if m["overdue_days"] is not None:
            word = "day" if m["overdue_days"] == 1 else "days"
            note = f'<span style="color:#cf222e">Overdue by {m["overdue_days"]} {word}</span>'
        elif status == "blocked":
            note = '<span style="color:#cf222e">Waiting on someone else</span>'
        rows.append(
            f"<tr><td>{esc(m['name'])}</td>"
            f'<td><span class="pill" style="background:{color}">'
            f"{STATUS_LABELS[status]}</span></td>"
            f"<td>{esc(m['due'])}</td><td>{note}</td></tr>"
        )
    attention = []
    for t in report.blocked_tasks:
        attention.append(
            f"<li><strong>Blocked task ({len(report.blocked_tasks)})</strong>"
            f"<br>{esc(t.get('name', 'Unnamed task'))} — owner: "
            f"{esc(t.get('owner', 'unassigned'))}"
            + (f"<br>Blocked by: {esc(t['blocked_by'])}" if t.get("blocked_by") else "")
            + "</li>"
        )
    for name, due, days in report.overdue:
        word = "day" if days == 1 else "days"
        attention.append(
            f"<li><strong>Overdue milestone ({len(report.overdue)})</strong>"
            f"<br>{esc(name)} — due {due.isoformat()} ({days} {word} overdue)</li>"
        )
    for name in report.failing_checks:
        attention.append(
            f"<li><strong>Failing check ({len(report.failing_checks)})</strong>"
            f"<br>{esc(name)}</li>"
        )
    if not attention:
        attention.append("<li><strong>All clear</strong><br>Nothing needs attention.</li>")
    still_needed = "".join(
        f"<li>{esc(name)}</li>" for name in report.unfinished_milestones
    ) + "".join(
        f"<li>Check failing: {esc(name)}</li>" for name in report.failing_checks
    )
    findings = "".join(f"<li>{esc(f)}</li>" for f in report.findings)
    badge_color = "#2da44e" if report.ready else "#cf222e"
    badge_text = "Ready for go-live" if report.ready else "Not ready for go-live"
    summary = (
        "Everything is done and all checks pass."
        if report.ready
        else (
            f"{len(report.unfinished_milestones)} milestone(s) still open"
            + (f", {len(report.blocked_tasks)} task(s) blocked" if report.blocked_tasks else "")
            + (f", {len(report.failing_checks)} check(s) failing" if report.failing_checks else "")
            + "."
        )
    )
    plan_line = f" ({esc(report.plan)})" if report.plan else ""
    target_line = (
        f"Target go-live: {esc(report.target_go_live)} &middot; "
        if report.target_go_live
        else ""
    )
    incomplete = report.total_milestones - c["done"]
    max_count = max(c.values()) or 1
    chart = "".join(
        f'<div class="chart-col"><div class="chart-val">{c[s]}</div>'
        f'<div class="chart-bar" style="height:{round(140 * c[s] / max_count)}px;'
        f'background:{color}"></div><div class="chart-label">{label}</div></div>'
        for s, label, color in (
            ("done", "Done", "#2da44e"),
            ("in_progress", "In progress", "#0969da"),
            ("not_started", "Not started", "#6e7781"),
            ("blocked", "Blocked", "#cf222e"),
        )
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Onboarding status — {esc(report.customer)}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  color: #1f2328; background: #f6f8fa; margin: 0; padding: 24px; }}
.page {{ max-width: 960px; margin: 0 auto; background: #fff;
  border: 1px solid #d0d7de; border-radius: 16px; padding: 28px 32px; }}
.eyebrow {{ font-size: 13px; font-weight: 700; letter-spacing: 1px; color: #656d76; }}
h1 {{ font-size: 26px; margin: 4px 0; }}
.meta {{ color: #656d76; font-size: 14px; margin: 0 0 20px; }}
.cards {{ display: flex; gap: 14px; flex-wrap: wrap; }}
.stat {{ flex: 1 1 180px; position: relative; overflow: hidden;
  border-radius: 12px; color: #fff; padding: 16px 18px; min-height: 84px; }}
.stat .label {{ font-size: 12px; font-weight: 700; letter-spacing: .5px;
  text-transform: uppercase; opacity: .9; }}
.stat .num {{ font-size: 40px; font-weight: 800; line-height: 1.1; }}
.stat .sub {{ font-size: 13px; opacity: .9; }}
.stat .deco {{ position: absolute; right: -22px; top: -22px; width: 96px; height: 96px;
  border-radius: 50%; background: rgba(255,255,255,.18); }}
.stat .deco2 {{ position: absolute; right: 22px; top: 8px; width: 56px; height: 56px;
  border-radius: 50%; background: rgba(255,255,255,.14); }}
.banner {{ margin-top: 18px; border-radius: 10px; color: #fff;
  background: {badge_color}; padding: 14px 18px; }}
.banner .t {{ font-size: 19px; font-weight: 700; }}
.banner .s {{ font-size: 14px; opacity: .95; }}
h2 {{ font-size: 16px; margin: 26px 0 10px; letter-spacing: .4px; }}
.bar {{ display: flex; height: 18px; border-radius: 9px; overflow: hidden;
  background: #eaeef2; }}
.bar span {{ display: block; height: 100%; }}
.legend {{ color: #656d76; font-size: 13px; margin-top: 6px; }}
.grid {{ display: flex; gap: 20px; flex-wrap: wrap; }}
.grid > div {{ flex: 1 1 380px; }}
.attention {{ background: linear-gradient(160deg, #e9e7fb 0%, #fdeede 60%, #fde8d8 100%);
  border-radius: 12px; padding: 18px 20px; }}
.attention ul {{ list-style: none; padding: 0; margin: 0; }}
.attention li {{ font-size: 14px; line-height: 1.55; padding: 10px 0;
  border-bottom: 1px solid rgba(31,35,40,.12); }}
.attention li:last-child {{ border-bottom: none; }}
.status-row {{ display: flex; align-items: center; gap: 14px; padding: 12px 0;
  border-bottom: 1px solid #d0d7de; }}
.status-row:last-child {{ border-bottom: none; }}
.icon {{ width: 44px; height: 44px; border-radius: 10px; color: #fff;
  font-size: 22px; font-weight: 700; display: flex; align-items: center;
  justify-content: center; flex-shrink: 0; }}
.status-row .num {{ font-size: 26px; font-weight: 800; }}
.status-row .cap {{ font-size: 12px; color: #656d76; letter-spacing: .4px;
  text-transform: uppercase; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th {{ background: #0b6fc4; color: #fff; text-align: left; padding: 9px 8px; }}
td {{ padding: 9px 8px; border-bottom: 1px solid #d0d7de; }}
tr:nth-child(even) td {{ background: #f6f8fa; }}
.pill {{ color: #fff; font-size: 12px; font-weight: 600; border-radius: 10px;
  padding: 2px 8px; white-space: nowrap; }}
.chart {{ display: flex; align-items: flex-end; gap: 22px; height: 210px;
  padding: 10px 8px 0; border-bottom: 2px solid #d0d7de; }}
.chart-col {{ flex: 1; display: flex; flex-direction: column; align-items: center;
  justify-content: flex-end; height: 100%; }}
.chart-val {{ font-weight: 700; font-size: 14px; margin-bottom: 4px; }}
.chart-bar {{ width: 52px; border-radius: 6px 6px 0 0; }}
.chart-label {{ font-size: 12px; color: #656d76; margin-top: 8px; text-align: center; }}
ul.plain {{ font-size: 14px; line-height: 1.6; padding-left: 20px; }}
footer {{ color: #656d76; font-size: 12px; margin-top: 28px; }}
</style>
</head>
<body>
<div class="page">
<p class="eyebrow">ONBOARDING STATUS</p>
<h1>Onboarding status — {esc(report.customer)}{plan_line}</h1>
<p class="meta">{target_line}Status as of {report.as_of.isoformat()}</p>
<div class="cards">
<div class="stat" style="background:linear-gradient(135deg,#2f7cf6,#5aa2ff)">
<div class="deco"></div><div class="deco2"></div>
<div class="label">Progress</div><div class="num">{report.progress_pct}%</div>
<div class="sub">of milestones done</div></div>
<div class="stat" style="background:linear-gradient(135deg,#9333ea,#b45cf0)">
<div class="deco"></div><div class="deco2"></div>
<div class="label">Milestones done</div><div class="num">{c['done']}</div>
<div class="sub">of {report.total_milestones} total</div></div>
<div class="stat" style="background:linear-gradient(135deg,#e11d48,#f472b6)">
<div class="deco"></div><div class="deco2"></div>
<div class="label">Blocked</div><div class="num">{c['blocked']}</div>
<div class="sub">milestone(s) waiting on others</div></div>
<div class="stat" style="background:linear-gradient(135deg,#ea580c,#fb923c)">
<div class="deco"></div><div class="deco2"></div>
<div class="label">Overdue</div><div class="num">{len(report.overdue)}</div>
<div class="sub">milestone(s) past due</div></div>
</div>
<div class="banner"><div class="t">{badge_text}</div>
<div class="s">{esc(summary)}</div></div>
<h2>Progress — {report.progress_pct}% complete</h2>
<div class="bar">{bar}</div>
<p class="legend">{c['done']} done &middot; {c['in_progress']} in progress &middot; {c['not_started']} not started &middot; {c['blocked']} blocked &middot; {report.total_milestones} milestones total</p>
<div class="grid">
<div>
<h2>What needs attention</h2>
<div class="attention"><ul>{''.join(attention)}</ul></div>
</div>
<div>
<h2>Onboarding status</h2>
<div class="status-row"><div class="icon" style="background:#0969da">&cir;</div>
<div><div class="num">{c['not_started']}</div>
<div class="cap">Not started (in queue)</div></div></div>
<div class="status-row"><div class="icon" style="background:#d29922">&#9684;</div>
<div><div class="num">{incomplete}</div>
<div class="cap">Incomplete milestones</div></div></div>
<div class="status-row"><div class="icon" style="background:#cf222e">!</div>
<div><div class="num">{len(report.overdue)}</div>
<div class="cap">Past due</div></div></div>
</div>
</div>
<div class="grid">
<div>
<h2>Milestones</h2>
<table>
<tr><th>Milestone</th><th>Status</th><th>Due</th><th>Notes</th></tr>
{''.join(rows)}
</table>
</div>
<div>
<h2>Milestones by status</h2>
<div class="chart">{chart}</div>
</div>
</div>
{f'<h2>What still needs to happen</h2><ul class="plain">{still_needed}</ul>' if still_needed else ''}
<h2>Findings</h2>
<ul class="plain">{findings}</ul>
<footer>Generated by saas-onboarding-automation (Python, standard library only).</footer>
</div>
</body>
</html>
"""


def report_to_dict(report):
    """JSON form of the report, carrying the same counts as the text one."""
    return {
        "customer": report.customer,
        "plan": report.plan,
        "target_go_live": report.target_go_live,
        "as_of": report.as_of.isoformat(),
        "milestones": {
            "total": report.total_milestones,
            "done": report.milestone_counts["done"],
            "in_progress": report.milestone_counts["in_progress"],
            "not_started": report.milestone_counts["not_started"],
            "blocked": report.milestone_counts["blocked"],
            "progress_pct": report.progress_pct,
        },
        "overdue_milestones": [
            {"name": name, "due": due.isoformat(), "days_overdue": days}
            for name, due, days in report.overdue
        ],
        "blocked_tasks": [
            {
                "name": t.get("name"),
                "milestone": t.get("milestone"),
                "owner": t.get("owner"),
                "blocked_by": t.get("blocked_by"),
            }
            for t in report.blocked_tasks
        ],
        "unfinished_milestones": report.unfinished_milestones,
        "failing_checks": report.failing_checks,
        "go_live_ready": report.ready,
        "findings": report.findings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report go-live readiness for a SaaS onboarding plan."
    )
    parser.add_argument("plan_file", help="onboarding plan JSON file")
    parser.add_argument(
        "--as-of",
        default=None,
        help="reference date YYYY-MM-DD for overdue checks (default: today)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--html",
        action="store_true",
        help="emit a visual HTML report for non-technical readers",
    )
    parser.add_argument("--output", default=None, help="write the report to this file")
    args = parser.parse_args(argv)

    as_of = parse_date(args.as_of) if args.as_of else date.today()
    if as_of is None:
        parser.error("--as-of must be a YYYY-MM-DD date")

    try:
        plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {args.plan_file}: {exc}", file=sys.stderr)
        return 2

    report = analyze(plan, as_of)
    if args.html:
        output = format_html(report)
    elif args.json:
        output = json.dumps(report_to_dict(report), indent=2) + "\n"
    else:
        output = format_text(report)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0 if report.ready else 1


if __name__ == "__main__":
    sys.exit(main())

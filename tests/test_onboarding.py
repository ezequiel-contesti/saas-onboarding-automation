import copy
import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import analyze, build_findings, format_html, format_text, report_to_dict

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "sample_onboarding.json"


def load_sample():
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


class ProgressTests(unittest.TestCase):
    def test_sample_progress_math(self):
        report = analyze(load_sample(), date(2026, 11, 14))
        self.assertEqual(report.total_milestones, 6)
        self.assertEqual(report.milestone_counts["done"], 2)
        self.assertEqual(report.milestone_counts["in_progress"], 1)
        self.assertEqual(report.milestone_counts["not_started"], 2)
        self.assertEqual(report.milestone_counts["blocked"], 1)
        self.assertEqual(report.progress_pct, 33)

    def test_overdue_only_counts_open_milestones(self):
        # As of Nov 14: Data migration (due Nov 12, in progress) is overdue.
        # The blocked CRM webhook (due Nov 13) is reported under blocked
        # tasks, not double-counted as overdue.
        report = analyze(load_sample(), date(2026, 11, 14))
        self.assertEqual(len(report.overdue), 1)
        self.assertEqual(report.overdue[0][0], "Data migration")
        self.assertEqual(report.overdue[0][2], 2)

    def test_not_overdue_before_due_date(self):
        report = analyze(load_sample(), date(2026, 11, 12))
        self.assertEqual(report.overdue, [])


class BlockedAndReadinessTests(unittest.TestCase):
    def test_blocked_task_extraction(self):
        report = analyze(load_sample(), date(2026, 11, 14))
        self.assertEqual(len(report.blocked_tasks), 1)
        task = report.blocked_tasks[0]
        self.assertEqual(task["owner"], "Customer IT")
        self.assertEqual(task["blocked_by"], "Customer firewall rule pending")

    def test_sample_is_not_ready(self):
        report = analyze(load_sample(), date(2026, 11, 14))
        self.assertFalse(report.ready)
        self.assertEqual(len(report.unfinished_milestones), 4)
        self.assertEqual(report.failing_checks, ["Webhook delivers test event"])

    def test_all_done_plan_is_ready(self):
        plan = load_sample()
        for milestone in plan["milestones"]:
            milestone["status"] = "done"
        for task in plan["tasks"]:
            task["status"] = "done"
        for check in plan["checks"]:
            check["status"] = "pass"
        report = analyze(plan, date(2026, 11, 14))
        self.assertTrue(report.ready)
        self.assertEqual(report.progress_pct, 100)
        self.assertEqual(report.overdue, [])
        self.assertEqual(report.blocked_tasks, [])

    def test_empty_plan_is_ready(self):
        report = analyze({"customer": "Nobody", "milestones": []}, date(2026, 11, 14))
        self.assertTrue(report.ready)


class OutputTests(unittest.TestCase):
    def test_text_report_matches_sample(self):
        report = analyze(load_sample(), date(2026, 11, 14))
        text = format_text(report)
        self.assertIn("Onboarding: Acme Energy (Enterprise) — target go-live 2026-11-20", text)
        self.assertIn("Milestones: 2 done, 1 in progress, 2 not started, 1 blocked of 6", text)
        self.assertIn("Data migration — due 2026-11-12 (2 days overdue)", text)
        self.assertIn("Go-live readiness: NOT READY", text)
        self.assertIn("Failing check: Webhook delivers test event", text)

    def test_json_carries_same_counts(self):
        report = analyze(load_sample(), date(2026, 11, 14))
        data = report_to_dict(report)
        self.assertEqual(data["milestones"]["done"], 2)
        self.assertEqual(data["milestones"]["progress_pct"], 33)
        self.assertFalse(data["go_live_ready"])
        self.assertEqual(len(data["overdue_milestones"]), 1)
        self.assertEqual(data["findings"], report.findings)

    def test_findings_mention_blocker_and_overdue(self):
        report = analyze(load_sample(), date(2026, 11, 14))
        joined = " ".join(report.findings)
        self.assertIn("Integration: CRM webhook", joined)
        self.assertIn("Data migration is overdue by 2 days", joined)


class HtmlReportTests(unittest.TestCase):
    def test_html_sample_is_self_contained_and_friendly(self):
        report = analyze(load_sample(), date(2026, 11, 14))
        page = format_html(report)
        self.assertIn("Onboarding status — Acme Energy", page)
        self.assertIn("Not ready for go-live", page)
        self.assertIn("Progress — 33% complete", page)
        self.assertIn("Data migration", page)
        self.assertIn("Overdue by 2 days", page)
        self.assertIn("Open firewall port 443 to webhook host", page)
        self.assertIn("Customer IT", page)
        self.assertIn("What still needs to happen", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("<script", page)

    def test_html_ready_plan_shows_ready_badge(self):
        plan = load_sample()
        for milestone in plan["milestones"]:
            milestone["status"] = "done"
        for task in plan["tasks"]:
            task["status"] = "done"
        for check in plan["checks"]:
            check["status"] = "pass"
        report = analyze(plan, date(2026, 11, 14))
        page = format_html(report)
        self.assertIn("Ready for go-live", page)
        self.assertIn("Progress — 100% complete", page)

    def test_html_escapes_plan_text(self):
        plan = load_sample()
        plan["customer"] = "Acme <b>Energy</b> & Co"
        report = analyze(plan, date(2026, 11, 14))
        page = format_html(report)
        self.assertIn("Acme &lt;b&gt;Energy&lt;/b&gt; &amp; Co", page)
        self.assertNotIn("<b>Energy</b>", page)

    def test_html_dashboard_sections(self):
        report = analyze(load_sample(), date(2026, 11, 14))
        page = format_html(report)
        self.assertIn("What needs attention", page)
        self.assertIn("Milestones by status", page)
        self.assertIn("Not started (in queue)", page)
        self.assertIn("Past due", page)
        self.assertIn("Blocked task (1)", page)


if __name__ == "__main__":
    unittest.main()

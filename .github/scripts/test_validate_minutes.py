#   Copyright 2026 UCP Authors
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""Unit tests for the meeting-minutes structure validator."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import validate_minutes

VALID_TC_MINUTES = (
    "# UCP Shopping Tech Council (STC) Meeting - 2026-10-09\n\n"
    "**Led by:** Amit Handa\n\n"
    "## Attendance\n\n"
    "- **Present:** Amit Handa, Ilya Grigorik\n\n"
    "## 1. General Topics\n\n"
    "- **Topic:** Food Domain Technical Council Draft Pull Request\n"
    "  - **Notes:** Published first draft pull request.\n"
    "  - **Context:** [Food PR]"
    "(https://github.com/Universal-Commerce-Protocol/ucp/pull/897)\n\n"
    "## 2. New Action Items\n\n"
    "- [ ] Review PR 897 (Due: 2026-10-16)\n"
)


class ValidateMinutesTest(unittest.TestCase):
    """Test suite for validate_minutes."""

    def test_should_validate_file(self) -> None:
        """Verify candidate meeting minutes path filtering."""
        self.assertTrue(
            validate_minutes.should_validate_file("tc/shopping/2026/2026-10-09.md")
        )
        self.assertTrue(
            validate_minutes.should_validate_file(
                "tc/shopping/dwg/bulk-product-discovery/2026/2026-09-16.md"
            )
        )
        self.assertTrue(
            validate_minutes.should_validate_file("gc/2026/GC-2026-08-07.md")
        )
        self.assertTrue(validate_minutes.should_validate_file("tc/wrong-place.md"))
        self.assertFalse(validate_minutes.should_validate_file("README.md"))
        self.assertFalse(validate_minutes.should_validate_file("tc/template.md"))
        self.assertFalse(validate_minutes.should_validate_file("tc/dwg-template.md"))
        self.assertFalse(validate_minutes.should_validate_file("gc/template.md"))
        self.assertFalse(validate_minutes.should_validate_file("tc/shopping/README.md"))

    def test_valid_tc_minutes_passes_with_zero_findings(self) -> None:
        """Verify canonical TC minutes pass with zero findings."""
        findings = validate_minutes.validate_minutes_markdown(
            VALID_TC_MINUTES,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        self.assertEqual(findings, [])

    def test_valid_dwg_and_gc_paths_pass(self) -> None:
        """Verify canonical DWG and GC minutes paths and headers pass."""
        dwg_md = VALID_TC_MINUTES.replace(
            "# UCP Shopping Tech Council (STC) Meeting - 2026-10-09",
            "# UCP Bulk Product Discovery DWG Meeting - 2026-09-16",
        ).replace("**Led by:** Amit Handa", "- **Led by:** Vinod Kumar")
        dwg_findings = validate_minutes.validate_minutes_markdown(
            dwg_md,
            rel_path="tc/shopping/dwg/bulk-product-discovery/2026/2026-09-16.md",
        )
        self.assertEqual(dwg_findings, [])

        gc_md = VALID_TC_MINUTES.replace(
            "# UCP Shopping Tech Council (STC) Meeting - 2026-10-09",
            "# UCP Governing Council (GC) Meeting 2026-08-07",
        )
        gc_findings = validate_minutes.validate_minutes_markdown(
            gc_md,
            rel_path="gc/2026/GC-2026-08-07.md",
        )
        self.assertEqual(gc_findings, [])

    def test_invalid_paths_and_dates_produce_errors(self) -> None:
        """Verify malformed paths, domains, and calendar dates report errors."""
        _, f1 = validate_minutes.validate_path_and_extract_date(
            "tc/unknown/2026/2026-10-09.md"
        )
        self.assertEqual([f.rule for f in f1], ["invalid-path"])

        _, f2 = validate_minutes.validate_path_and_extract_date(
            "tc/shopping/2025/2026-10-09.md"
        )
        self.assertEqual([f.rule for f in f2], ["invalid-path"])

        _, f3 = validate_minutes.validate_path_and_extract_date("gc/2026/2026-08-07.md")
        self.assertEqual([f.rule for f in f3], ["invalid-path"])

        _, f4 = validate_minutes.validate_path_and_extract_date(
            "tc/shopping/2026/2026-02-30.md"
        )
        self.assertEqual([f.rule for f in f4], ["invalid-date"])

    def test_detects_title_date_mismatch_and_missing_sections(self) -> None:
        """Verify missing metadata, sections, and date mismatches fail."""
        bad_md = (
            "# UCP Shopping Tech Council (STC) Meeting - 2026-07-31\n\n"
            "## 1. General Topics\n\n"
            "- **Topic:** Something\n"
            "  - **Notes:** Details.\n"
        )
        findings = validate_minutes.validate_minutes_markdown(
            bad_md,
            rel_path="tc/shopping/2026/2026-08-31.md",
        )
        rules = {f.rule for f in findings}
        self.assertIn("title-date-mismatch", rules)
        self.assertIn("missing-led-by", rules)
        self.assertIn("missing-attendance", rules)
        self.assertTrue(all(f.severity == "error" for f in findings))

    def test_detects_non_ucp_or_late_h1_title(self) -> None:
        """Verify H1 must start with UCP and appear before H2 sections."""
        non_ucp_h1 = VALID_TC_MINUTES.replace(
            "# UCP Shopping Tech Council (STC) Meeting - 2026-10-09",
            "# Shopping Tech Council (STC) Meeting - 2026-10-09",
        )
        findings_prefix = validate_minutes.validate_minutes_markdown(
            non_ucp_h1,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        self.assertEqual(
            [f.rule for f in findings_prefix],
            ["missing-h1-title"],
        )

        late_h1 = (
            "**Led by:** Amit Handa\n\n"
            "## Attendance\n\n"
            "- **Present:** Amit Handa\n\n"
            "## 1. General Topics\n\n"
            "# UCP Shopping Tech Council (STC) Meeting - 2026-10-09\n"
        )
        findings_late = validate_minutes.validate_minutes_markdown(
            late_h1,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        self.assertEqual([f.rule for f in findings_late], ["missing-h1-title"])

    def test_detects_template_placeholders_and_malformed_topics(self) -> None:
        """Verify template placeholders and non-canonical topics fail."""
        bad_md = (
            "# UCP {DOMAIN} Tech Council Meeting - 2026-10-09\n\n"
            "**Led by:** {NAME}\n\n"
            "## Attendance\n\n"
            "- **Present:** Alice\n\n"
            "## 1. Discussions\n\n"
            "- Topic: Unbolded topic\n"
            "  - **Context:** [Link to PR](#)\n"
            "- **Topic**: Colon outside bold\n"
        )
        findings = validate_minutes.validate_minutes_markdown(
            bad_md,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        rules = [f.rule for f in findings if f.severity == "error"]
        self.assertEqual(rules.count("unfilled-template-placeholder"), 3)
        self.assertEqual(rules.count("malformed-topic-marker"), 2)

    def test_ignores_inline_code_and_nested_code_fences(self) -> None:
        """Verify inline code spans and nested fences do not false-positive."""
        md_with_code = (
            "# UCP Shopping Tech Council (STC) Meeting - 2026-10-09\n\n"
            "**Led by:** Amit Handa\n\n"
            "## Attendance\n\n"
            "- **Present:** Amit Handa\n\n"
            "## 1. General Topics\n\n"
            "- **Topic:** URI Template Standardization\n"
            "  - **Notes:** Discussed `{DOMAIN}`, `{STATUS}`, `[link](#)`, "
            "and `PR #123` in inline code.\n\n"
            "````markdown\n"
            "- Topic: Inside outer 4-backtick fence\n"
            "```json\n"
            '{"placeholder": "{DOMAIN}"}\n'
            "```\n"
            "[dummy](#)\n"
            "````\n"
        )
        findings = validate_minutes.validate_minutes_markdown(
            md_with_code,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        self.assertEqual(findings, [])

    def test_closes_topic_on_non_list_top_level_blocks(self) -> None:
        """Verify top-level blockquotes or subheadings close active topics."""
        md_with_blockquote = (
            "# UCP Shopping Tech Council (STC) Meeting - 2026-10-09\n\n"
            "**Led by:** Amit Handa\n\n"
            "## Attendance\n\n"
            "- **Present:** Amit Handa\n\n"
            "## 1. General Topics\n\n"
            "- **Topic:** Empty Topic Before Blockquote\n\n"
            "> **Note:** DWGs operate under DTC oversight.\n"
            "  Indented continuation of blockquote mentioning PR #555.\n"
        )
        findings = validate_minutes.validate_minutes_markdown(
            md_with_blockquote,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        self.assertEqual([f.rule for f in findings], ["empty-topic-block"])

    def test_emits_soft_warnings_for_unlinked_pr_and_empty_topic(self) -> None:
        """Verify unlinked PR mentions and empty topics emit soft warnings."""
        warn_md = (
            "# UCP Shopping Tech Council (STC) Meeting - 2026-10-09\n\n"
            "**Led by:** Amit Handa\n\n"
            "## Attendance\n\n"
            "- **Present:** Amit Handa\n\n"
            "## 1. General Topics\n\n"
            "- **Topic:** Announcement Only Topic\n"
            "- **Topic:** Webhook Signing Discussion\n"
            "  - **Notes:** Discussed PR #659 and pull request 919.\n"
            "  - **Context:** [PR 659]"
            "(https://github.com/Universal-Commerce-Protocol/ucp/pull/659)\n"
        )
        findings = validate_minutes.validate_minutes_markdown(
            warn_md,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        self.assertTrue(all(f.severity == "warning" for f in findings))
        by_rule = {f.rule: f for f in findings}
        self.assertIn("empty-topic-block", by_rule)
        self.assertIn("unlinked-pr-or-issue", by_rule)
        self.assertIn("#919", by_rule["unlinked-pr-or-issue"].message)
        self.assertNotIn("#659", by_rule["unlinked-pr-or-issue"].message)

    def test_github_annotation_escapes_special_characters(self) -> None:
        """Verify workflow command escaping for properties and message data."""
        finding = validate_minutes.Finding(
            rel_path="tc/shopping/2026/bad:file,name%.md",
            line=10,
            severity="warning",
            rule="custom:rule,1",
            message="Line 1\nLine 2\r100% done",
        )
        self.assertEqual(
            finding.format_github_annotation(),
            "::warning file=tc/shopping/2026/bad%3Afile%2Cname%25.md,"
            "line=10,title=custom%3Arule%2C1::Line 1%0ALine 2%0D100%25 done",
        )

    def test_main_returns_zero_on_warnings_and_nonzero_on_errors(self) -> None:
        """Verify CLI exit code is 0 for warnings and 1 for hard errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            minutes_path = root / "tc" / "shopping" / "2026" / "2026-10-09.md"
            minutes_path.parent.mkdir(parents=True)
            summary_path = root / "summary.md"

            # 1. Valid file with a soft warning -> exit code 0
            warn_md = VALID_TC_MINUTES.replace(
                "Published first draft pull request.",
                "Published first draft pull request and discussed PR #999.",
            )
            minutes_path.write_text(warn_md, encoding="utf-8")
            rc_warn = validate_minutes.main(
                [
                    "--repo-root",
                    str(root),
                    "--summary-file",
                    str(summary_path),
                    "tc/shopping/2026/2026-10-09.md",
                ]
            )
            self.assertEqual(rc_warn, 0)
            self.assertIn(
                "unlinked-pr-or-issue",
                summary_path.read_text(encoding="utf-8"),
            )

            # 2. Invalid file with a hard error -> exit code 1
            bad_md = VALID_TC_MINUTES.replace("2026-10-09", "2026-09-99")
            minutes_path.write_text(bad_md, encoding="utf-8")
            rc_err = validate_minutes.main(
                [
                    "--repo-root",
                    str(root),
                    "tc/shopping/2026/2026-10-09.md",
                ]
            )
            self.assertEqual(rc_err, 1)


if __name__ == "__main__":
    unittest.main()

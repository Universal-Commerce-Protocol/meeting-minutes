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

"""Unit tests for the meeting-minutes circle-back script."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import circle_back


SAMPLE_MINUTES = """\
# UCP Shopping Tech Council (STC) Meeting - 2026-10-09

**Led by:** Amit Handa

- **Charter:** [DWG Charter](https://github.com/Universal-Commerce-Protocol/ucp/issues/682)

## Attendance

- **Present:** Amit Handa, Ilya Grigorik ([roster](https://github.com/Universal-Commerce-Protocol/ucp/issues/682))

## 1. General Topics

- **Topic:** Bonus Deep Dives and Food Domain Technical Council Draft Pull Request
  - **Notes:** Published first draft pull requests.
  - **Context:** [Food PR](https://github.com/Universal-Commerce-Protocol/ucp/pull/897), [Lodging PR](https://github.com/Universal-Commerce-Protocol/ucp/pull/780)

## 2. Open Discussions & PR Reviews

- **Topic:** Ask Capability — Authority Boundaries
  - **Notes:** Discussed authority boundaries.
  - **Context:** [Ask PR](https://github.com/Universal-Commerce-Protocol/ucp/pull/538)
- **Topic:** Ask Capability — Prompt Injection
  - **Notes:** Discussed sanitization.
  - **Context:** [Ask PR](https://github.com/Universal-Commerce-Protocol/ucp/pull/538), [JS SDK PR](https://github.com/Universal-Commerce-Protocol/js-sdk/pull/87)
- **Topic:** Subscriptions and Proposal Issue
  - **Context:** [Sub Issue](https://github.com/Universal-Commerce-Protocol/ucp/issues/629), [Self PR](https://github.com/Universal-Commerce-Protocol/meeting-minutes/pull/47)

## 3. New Action Items

- [ ] Review [PR 999](https://github.com/Universal-Commerce-Protocol/ucp/pull/999)
"""


class CircleBackTest(unittest.TestCase):

    def test_is_meeting_minutes_file(self) -> None:
        self.assertTrue(
            circle_back.is_meeting_minutes_file("tc/shopping/2026/2026-10-09.md")
        )
        self.assertTrue(
            circle_back.is_meeting_minutes_file(
                "tc/shopping/dwg/bulk-product-discovery/2026/2026-09-16.md"
            )
        )
        self.assertTrue(
            circle_back.is_meeting_minutes_file("gc/2026/GC-2026-08-07.md")
        )
        self.assertFalse(circle_back.is_meeting_minutes_file("README.md"))
        self.assertFalse(circle_back.is_meeting_minutes_file("tc/template.md"))
        self.assertFalse(circle_back.is_meeting_minutes_file("tc/dwg-template.md"))
        self.assertFalse(circle_back.is_meeting_minutes_file("gc/template.md"))
        self.assertFalse(
            circle_back.is_meeting_minutes_file("tc/shopping/README.md")
        )

    def test_github_heading_slug(self) -> None:
        self.assertEqual(
            circle_back.github_heading_slug("1. General Topics"),
            "1-general-topics",
        )
        self.assertEqual(
            circle_back.github_heading_slug("2. Open Discussions & PR Reviews"),
            "2-open-discussions--pr-reviews",
        )

    def test_parse_minutes_markdown_extracts_and_consolidates_topics(self) -> None:
        parsed = circle_back.parse_minutes_markdown(
            SAMPLE_MINUTES,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        self.assertEqual(
            parsed.meeting_title,
            "UCP Shopping Tech Council (STC) Meeting - 2026-10-09",
        )
        refs = {t.short_ref: t for t in parsed.targets}
        # Charter link in preamble/Attendance, self link to meeting-minutes#47,
        # and action items outside a Topic block should all be ignored.
        self.assertEqual(
            list(refs.keys()),
            ["ucp#897", "ucp#780", "ucp#538", "js-sdk#87", "ucp#629"],
        )
        # ucp#538 was referenced under two distinct topics in section 2
        self.assertEqual(
            [topic.title for topic in refs["ucp#538"].topics],
            [
                "Ask Capability — Authority Boundaries",
                "Ask Capability — Prompt Injection",
            ],
        )
        self.assertEqual(
            refs["ucp#538"].topics[0].section_slug,
            "2-open-discussions--pr-reviews",
        )
        self.assertEqual(refs["ucp#629"].item_noun, "issue")
        self.assertEqual(refs["ucp#897"].item_noun, "pull request")

    def test_format_comment_option_b(self) -> None:
        parsed = circle_back.parse_minutes_markdown(
            SAMPLE_MINUTES,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        refs = {t.short_ref: t for t in parsed.targets}
        body = circle_back.format_comment(parsed, refs["ucp#538"])
        self.assertIn(
            "<!-- ucp-meeting-minutes: tc/shopping/2026/2026-10-09.md -->",
            body,
        )
        self.assertIn(
            "📋 **Discussed in [UCP Shopping Tech Council (STC) Meeting - 2026-10-09]"
            "(https://github.com/Universal-Commerce-Protocol/meeting-minutes/blob/main/tc/shopping/2026/2026-10-09.md)**",
            body,
        )
        self.assertIn(
            "This pull request was referenced under the following agenda topic(s):",
            body,
        )
        self.assertIn(
            "* **Ask Capability — Authority Boundaries** ([view section]"
            "(https://github.com/Universal-Commerce-Protocol/meeting-minutes/blob/main/tc/shopping/2026/2026-10-09.md#2-open-discussions--pr-reviews))",
            body,
        )
        self.assertIn(
            "* **Ask Capability — Prompt Injection** ([view section]"
            "(https://github.com/Universal-Commerce-Protocol/meeting-minutes/blob/main/tc/shopping/2026/2026-10-09.md#2-open-discussions--pr-reviews))",
            body,
        )

    def test_process_minutes_skips_closed_merged_and_existing(self) -> None:
        parsed = circle_back.parse_minutes_markdown(
            SAMPLE_MINUTES,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        fake_client = mock.MagicMock(spec=circle_back.GitHubApiClient)

        states = {
            "ucp#897": "open",
            "ucp#780": "open",
            "ucp#538": "merged",
            "js-sdk#87": "open",
            "ucp#629": "closed",
        }
        fake_client.get_target_state.side_effect = (
            lambda t: states[t.short_ref]
        )
        # Pretend ucp#780 already has the comment marker
        fake_client.has_comment_with_marker.side_effect = (
            lambda t, marker: t.short_ref == "ucp#780"
        )

        # 1. Dry-run mode
        dry_results = circle_back.process_minutes(
            parsed, client=fake_client, apply=False
        )
        actions_by_ref = {r.target.short_ref: r.action for r in dry_results}
        self.assertEqual(
            actions_by_ref,
            {
                "ucp#897": "would_post",
                "ucp#780": "skipped_existing",
                "ucp#538": "skipped_merged",
                "js-sdk#87": "would_post",
                "ucp#629": "skipped_closed",
            },
        )
        fake_client.post_comment.assert_not_called()

        # 2. Apply mode
        apply_results = circle_back.process_minutes(
            parsed, client=fake_client, apply=True
        )
        apply_actions_by_ref = {
            r.target.short_ref: r.action for r in apply_results
        }
        self.assertEqual(
            apply_actions_by_ref,
            {
                "ucp#897": "posted",
                "ucp#780": "skipped_existing",
                "ucp#538": "skipped_merged",
                "js-sdk#87": "posted",
                "ucp#629": "skipped_closed",
            },
        )
        self.assertEqual(fake_client.post_comment.call_count, 2)

    def test_render_markdown_summary(self) -> None:
        parsed = circle_back.parse_minutes_markdown(
            SAMPLE_MINUTES,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        results = [
            circle_back.TargetResult(
                target=parsed.targets[0],
                state="open",
                action="would_post",
                comment_body=circle_back.format_comment(
                    parsed, parsed.targets[0]
                ),
            ),
            circle_back.TargetResult(
                target=parsed.targets[2],
                state="merged",
                action="skipped_merged",
                comment_body=circle_back.format_comment(
                    parsed, parsed.targets[2]
                ),
            ),
        ]
        summary = circle_back.render_markdown_summary(
            [(parsed, results)], apply=False
        )
        self.assertIn("<!-- ucp-meeting-minutes-preview -->", summary)
        self.assertIn("`ucp#897`", summary)
        self.assertIn("🟢 Open", summary)
        self.assertIn("📝 Will post comment on merge", summary)
        self.assertIn("🟣 Merged", summary)
        self.assertIn("⏭️ Skipped (merged)", summary)

    def test_main_cli_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            minutes_file = root / "tc" / "shopping" / "2026" / "2026-10-09.md"
            minutes_file.parent.mkdir(parents=True)
            minutes_file.write_text(SAMPLE_MINUTES, encoding="utf-8")
            summary_file = root / "summary.md"

            with mock.patch.object(
                circle_back.GitHubApiClient,
                "get_target_state",
                return_value="open",
            ), mock.patch.object(
                circle_back.GitHubApiClient,
                "has_comment_with_marker",
                return_value=False,
            ):
                rc = circle_back.main(
                    [
                        "--repo-root",
                        str(root),
                        "--summary-file",
                        str(summary_file),
                        "tc/shopping/2026/2026-10-09.md",
                        "README.md",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertTrue(summary_file.is_file())
            text = summary_file.read_text(encoding="utf-8")
            self.assertIn("ucp#897", text)

    def test_resolve_github_event_config(self) -> None:
        root = pathlib.Path(".")
        with mock.patch.object(
            circle_back,
            "git_changed_minutes_files",
            return_value=["tc/shopping/2026/2026-10-09.md"],
        ) as mock_diff:
            files, apply, preview_pr = circle_back.resolve_github_event_config(
                {
                    "EVENT_NAME": "pull_request",
                    "BASE_SHA": "abc123",
                    "PR_NUMBER": "49",
                },
                root,
            )
            self.assertEqual(files, ["tc/shopping/2026/2026-10-09.md"])
            self.assertFalse(apply)
            self.assertEqual(preview_pr, 49)
            mock_diff.assert_called_once_with("abc123", root)

            files, apply, preview_pr = circle_back.resolve_github_event_config(
                {"EVENT_NAME": "push", "BEFORE_SHA": "def456"},
                root,
            )
            self.assertEqual(files, ["tc/shopping/2026/2026-10-09.md"])
            self.assertTrue(apply)
            self.assertIsNone(preview_pr)

        files, apply, preview_pr = circle_back.resolve_github_event_config(
            {
                "EVENT_NAME": "workflow_dispatch",
                "INPUT_MINUTES_FILE": "tc/shopping/2026/2026-10-09.md",
                "INPUT_APPLY": "true",
            },
            root,
        )
        self.assertEqual(files, ["tc/shopping/2026/2026-10-09.md"])
        self.assertTrue(apply)
        self.assertIsNone(preview_pr)


if __name__ == "__main__":
    unittest.main()

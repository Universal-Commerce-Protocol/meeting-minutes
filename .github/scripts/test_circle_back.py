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

SAMPLE_MINUTES = (
    "# UCP Shopping Tech Council (STC) Meeting - 2026-10-09\n\n"
    "**Led by:** Amit Handa\n\n"
    "- **Charter:** [DWG Charter]"
    "(https://github.com/Universal-Commerce-Protocol/ucp/issues/682)\n\n"
    "## Attendance\n\n"
    "- **Present:** Amit Handa, Ilya Grigorik "
    "([roster](https://github.com/Universal-Commerce-Protocol/ucp/issues/682))\n\n"
    "## 1. General Topics\n\n"
    "- **Topic:** Bonus Deep Dives and Food Domain Technical Council "
    "Draft Pull Request\n"
    "  - **Notes:** Published first draft pull requests.\n"
    "  - **Context:** [Food PR]"
    "(https://github.com/Universal-Commerce-Protocol/ucp/pull/897), "
    "[Lodging PR]"
    "(https://github.com/Universal-Commerce-Protocol/ucp/pull/780)\n\n"
    "## 2. Open Discussions & PR Reviews\n\n"
    "- **Topic:** Ask Capability — Authority Boundaries\n"
    "  - **Notes:** Discussed authority boundaries.\n"
    "  - **Context:** [Ask PR]"
    "(https://github.com/Universal-Commerce-Protocol/ucp/pull/538)\n"
    "- **Topic:** Ask Capability — Prompt Injection\n"
    "  - **Notes:** Discussed sanitization.\n"
    "  - **Context:** [Ask PR]"
    "(https://github.com/Universal-Commerce-Protocol/ucp/pull/538), "
    "[JS SDK PR]"
    "(https://github.com/Universal-Commerce-Protocol/js-sdk/pull/87)\n"
    "- **Topic:** Subscriptions and Proposal Issue\n"
    "  - **Context:** [Sub Issue]"
    "(https://github.com/Universal-Commerce-Protocol/ucp/issues/629), "
    "[Self PR]"
    "(https://github.com/Universal-Commerce-Protocol/meeting-minutes/pull/47)\n\n"
    "## 3. New Action Items\n\n"
    "- [ ] Review [PR 999]"
    "(https://github.com/Universal-Commerce-Protocol/ucp/pull/999)\n"
)


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
        self.assertTrue(circle_back.is_meeting_minutes_file("gc/2026/GC-2026-08-07.md"))
        self.assertFalse(circle_back.is_meeting_minutes_file("README.md"))
        self.assertFalse(circle_back.is_meeting_minutes_file("tc/template.md"))
        self.assertFalse(circle_back.is_meeting_minutes_file("tc/dwg-template.md"))
        self.assertFalse(circle_back.is_meeting_minutes_file("gc/template.md"))
        self.assertFalse(circle_back.is_meeting_minutes_file("tc/shopping/README.md"))
        self.assertFalse(
            circle_back.is_meeting_minutes_file(".github/scripts/circle_back.py")
        )
        self.assertEqual(
            circle_back._normalize_rel_path("./.github/scripts/circle_back.py"),
            ".github/scripts/circle_back.py",
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
        fake_client.get_target_state.side_effect = lambda t: states[t.short_ref]
        # Pretend ucp#780 already has the comment marker
        fake_client.has_comment_with_marker.side_effect = lambda t, marker: (
            t.short_ref == "ucp#780"
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
        apply_actions_by_ref = {r.target.short_ref: r.action for r in apply_results}
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
                comment_body=circle_back.format_comment(parsed, parsed.targets[0]),
            ),
            circle_back.TargetResult(
                target=parsed.targets[2],
                state="merged",
                action="skipped_merged",
                comment_body=circle_back.format_comment(parsed, parsed.targets[2]),
            ),
        ]
        summary = circle_back.render_markdown_summary([(parsed, results)], apply=False)
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

            with (
                mock.patch.object(
                    circle_back.GitHubApiClient,
                    "get_target_state",
                    return_value="open",
                ),
                mock.patch.object(
                    circle_back.GitHubApiClient,
                    "has_comment_with_marker",
                    return_value=False,
                ),
            ):
                rc = circle_back.main(
                    [
                        "--repo-root",
                        str(root),
                        "--summary-file",
                        str(summary_file),
                        "tc/shopping/2026/2026-10-09.md",
                        "README.md",
                        "../outside/tc/2026-10-09.md",
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

    def test_parse_minutes_markdown_handles_heading_and_topic_variants(
        self,
    ) -> None:
        variant_md = (
            "### UCP Tech Council (TC) Meeting - 2026-03-06\n\n"
            "#### 2. PR Reviews\n\n"
            "- Topic: Identity Linking (PR #354) "
            "(<https://github.com/Universal-Commerce-Protocol/ucp/issues/354>)\n"
            "  - **Context:** [PR 354]"
            "(https://github.com/universal-commerce-protocol/ucp/pull/354)\n"
            "- **Topic**: [Add Foo | Bar]"
            "(https://github.com/Universal-Commerce-Protocol/ucp/pull/400)\n"
            "- [ ] Unrelated action item for [PR 999]"
            "(https://github.com/Universal-Commerce-Protocol/ucp/pull/999)\n\n"
            "#### 3. New Action Items\n\n"
            "- [ ] Review [PR 888]"
            "(https://github.com/Universal-Commerce-Protocol/ucp/pull/888)\n"
        )
        parsed = circle_back.parse_minutes_markdown(
            variant_md,
            rel_path="tc/shopping/2026/2026-03-06.md",
        )
        self.assertEqual(
            parsed.meeting_title,
            "UCP Tech Council (TC) Meeting - 2026-03-06",
        )
        refs = {t.short_ref: t for t in parsed.targets}
        self.assertEqual(list(refs.keys()), ["ucp#354", "ucp#400"])
        self.assertEqual(refs["ucp#354"].kind, "pull")
        self.assertEqual(
            refs["ucp#354"].topics[0].title,
            "Identity Linking (PR #354)",
        )
        self.assertEqual(
            refs["ucp#354"].topics[0].section_slug,
            "2-pr-reviews",
        )
        self.assertEqual(
            refs["ucp#400"].topics[0].title,
            "Add Foo | Bar",
        )

    def test_get_target_state_upgrades_issue_link_for_pr(self) -> None:
        client = circle_back.GitHubApiClient(token="fake")
        target = circle_back.CircleBackTarget(
            org="Universal-Commerce-Protocol",
            repo="ucp",
            kind="issues",
            number=481,
        )
        second_target = circle_back.CircleBackTarget(
            org="Universal-Commerce-Protocol",
            repo="ucp",
            kind="issues",
            number=481,
        )
        with mock.patch.object(
            client,
            "_request",
            return_value={
                "state": "closed",
                "pull_request": {"merged_at": "2026-07-01T00:00:00Z"},
            },
        ) as mock_req:
            state = client.get_target_state(target)
            cached_state = client.get_target_state(second_target)
        self.assertEqual(state, "merged")
        self.assertEqual(cached_state, "merged")
        self.assertEqual(target.kind, "pull")
        self.assertEqual(target.item_noun, "pull request")
        self.assertEqual(second_target.kind, "pull")
        self.assertEqual(second_target.item_noun, "pull request")
        mock_req.assert_called_once()

    def test_clean_topic_title_preserves_multiple_bold_spans(self) -> None:
        self.assertEqual(
            circle_back._clean_topic_title("**Single Bold Topic**"),
            "Single Bold Topic",
        )
        self.assertEqual(
            circle_back._clean_topic_title(
                "**Ask Capability** and **Cart Capability**"
            ),
            "**Ask Capability** and **Cart Capability**",
        )

    def test_parse_minutes_ignores_inline_code_and_mismatched_fences(self) -> None:
        md = (
            "# UCP Shopping Tech Council (STC) Meeting - 2026-10-09\n\n"
            "## 1. General Topics\n\n"
            "- **Topic:** Inline Code and Fences\n"
            "  - **Notes:** Ignore `https://github.com/Universal-Commerce-Protocol/ucp/pull/777` in backticks.\n"
            "    ~~~markdown\n"
            "    ```\n"
            "    https://github.com/Universal-Commerce-Protocol/ucp/pull/888\n"
            "    ```\n"
            "    ~~~\n"
            "  - **Context:** [Real PR](https://github.com/Universal-Commerce-Protocol/ucp/pull/897)\n"
        )
        parsed = circle_back.parse_minutes_markdown(
            md,
            rel_path="tc/shopping/2026/2026-10-09.md",
        )
        self.assertEqual([t.short_ref for t in parsed.targets], ["ucp#897"])

    def test_git_changed_minutes_files_uses_merge_flag_on_diff_tree(self) -> None:
        root = pathlib.Path(".")
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0,
                stdout="tc/shopping/2026/2026-10-09.md\n",
                stderr="",
            )
            files = circle_back.git_changed_minutes_files(
                circle_back.ZERO_SHA,
                root,
            )
        self.assertEqual(files, ["tc/shopping/2026/2026-10-09.md"])
        called_cmd = mock_run.call_args[0][0]
        self.assertIn("diff-tree", called_cmd)
        self.assertIn("-m", called_cmd)
        self.assertIn("--first-parent", called_cmd)

    def test_render_markdown_summary_escapes_table_pipes(self) -> None:
        target = circle_back.CircleBackTarget(
            org="Universal-Commerce-Protocol",
            repo="ucp",
            kind="pull",
            number=400,
            topics=[circle_back.TopicRef(title="Add Foo | Bar", section_slug="s")],
        )
        parsed = circle_back.ParsedMinutes(
            rel_path="tc/shopping/2026/2026-10-09.md",
            meeting_title="Test Meeting",
            targets=[target],
        )
        res = circle_back.TargetResult(
            target=target,
            state="unknown",
            action="error",
            comment_body="body",
            detail="HTTP 500 | Bad\nGateway",
        )
        summary = circle_back.render_markdown_summary([(parsed, [res])], apply=False)
        self.assertIn(r"• *Add Foo \| Bar*", summary)
        self.assertIn(r"(`HTTP 500 \| Bad Gateway`)", summary)


if __name__ == "__main__":
    unittest.main()

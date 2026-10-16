#!/usr/bin/env python3
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

"""Structural validator for UCP meeting minutes Markdown files.

Enforces a two-tier validation model on added or modified meeting minutes under
`tc/` and `gc/`:
  - Hard Errors (exit code 1): path/date schema, H1 title and date consistency,
    required metadata (`**Led by:**`, `## Attendance`, `## ` agenda sections),
    leftover template placeholders (`{...}` or `](#)`), and malformed topic
    markers (`- Topic:`, `- **Topic**:`).
  - Soft Warnings (exit code 0): unlinked PR/Issue mentions inside a
    `- **Topic:**` block (which would be missed by Circle-Back automation) and
    empty `- **Topic:**` entries with no indented sub-bullets.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("validate_minutes")

VALID_DOMAINS = frozenset({"shopping", "food", "lodging", "payments"})
IGNORED_FILES = frozenset(
    {
        "tc/template.md",
        "tc/dwg-template.md",
        "gc/template.md",
    }
)

TC_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
GC_FILENAME_RE = re.compile(r"^GC-(\d{4}-\d{2}-\d{2})\.md$")
WG_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE_IN_TEXT_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
LED_BY_RE = re.compile(r"^\s*(?:[-*+]\s+)?\*\*Led by:\*\*\s*(.+?)\s*$")

CANONICAL_TOPIC_RE = re.compile(r"^(\s*)[-*+]\s+\*\*Topic:\*\*\s*(.+?)\s*$")
EMPTY_TOPIC_RE = re.compile(r"^\s*[-*+]\s+\*\*Topic:\*\*\s*$")
MALFORMED_TOPIC_RE = re.compile(
    r"^\s*(?:[-*+]\s+(?:\*\*Topic\*\*:|Topic:)\s*(.*)|\*\*Topic:\*\*\s*(.*))$"
)
LIST_ITEM_RE = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+")

TEMPLATE_PLACEHOLDER_RE = re.compile(
    r"\{(?:DOMAIN|DOMAIN_ABBR|YYYY-MM-DD|NAME|LIST_NAMES|TOPIC_NAME|"
    r"DWG_NAME|CHAIR_OR_CO_CHAIR|BRIEF_SUMMARY_OF_POINTS|DECISION|POSITION|"
    r"KEY_TAKEAWAYS|TASK_DESCRIPTION|DATE|ITEM|OWNER|STATUS|"
    r"DELIVERABLE_NAME|YES_OR_NO)\}"
)
DUMMY_LINK_RE = re.compile(r"\[[^\]]+\]\(#\)")

GH_ITEM_LINK_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/"
    r"(?:pull|issues|discussions)/(\d+)(?![0-9])"
)
PROSE_ITEM_MENTION_RE = re.compile(
    r"\b(?:PRs?|pull requests?|issues?|RFCs?)\s+#?(\d+)\b",
    re.IGNORECASE,
)

ZERO_SHA = "0000000000000000000000000000000000000000"
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


@dataclass(frozen=True)
class Finding:
    """A single validation error or warning on a meeting minutes file."""

    rel_path: str
    line: int
    severity: str  # "error" or "warning"
    rule: str
    message: str

    def format_cli(self) -> str:
        prefix = "ERROR" if self.severity == "error" else "WARNING"
        return f"{self.rel_path}:{self.line}: {prefix} [{self.rule}] {self.message}"

    def format_github_annotation(self) -> str:
        cmd = "error" if self.severity == "error" else "warning"
        return (
            f"::{cmd} file={self.rel_path},line={self.line},"
            f"title={self.rule}::{self.message}"
        )


def should_validate_file(rel_path: str) -> bool:
    """Returns True if rel_path is a candidate meeting minutes Markdown file."""
    norm = rel_path.strip().lstrip("./")
    if not norm.endswith(".md") or norm in IGNORED_FILES:
        return False
    parts = Path(norm).parts
    if len(parts) < 2 or parts[0] not in ("tc", "gc"):
        return False
    return parts[-1].lower() != "readme.md"


def _parse_iso_date(date_str: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(date_str)
    except ValueError:
        return None


def validate_path_and_extract_date(
    rel_path: str,
) -> tuple[str | None, list[Finding]]:
    """Validates the repository path schema and extracts the expected YYYY-MM-DD."""
    norm = rel_path.strip().lstrip("./")
    parts = Path(norm).parts
    findings: list[Finding] = []

    if parts[0] == "gc":
        # Expected: gc/<YYYY>/GC-<YYYY-MM-DD>.md
        if len(parts) != 3:
            findings.append(
                Finding(
                    rel_path=norm,
                    line=1,
                    severity="error",
                    rule="invalid-path",
                    message=(
                        f"GC minutes must be placed at 'gc/<YYYY>/GC-<YYYY-MM-DD>.md' "
                        f"(got '{norm}')."
                    ),
                )
            )
            return None, findings

        year_dir, filename = parts[1], parts[2]
        m = GC_FILENAME_RE.match(filename)
        if not m:
            findings.append(
                Finding(
                    rel_path=norm,
                    line=1,
                    severity="error",
                    rule="invalid-path",
                    message=(
                        f"GC minutes filename must match 'GC-YYYY-MM-DD.md' "
                        f"(got '{filename}')."
                    ),
                )
            )
            return None, findings

        date_str = m.group(1)
        if not _parse_iso_date(date_str):
            findings.append(
                Finding(
                    rel_path=norm,
                    line=1,
                    severity="error",
                    rule="invalid-date",
                    message=f"Filename contains invalid calendar date '{date_str}'.",
                )
            )
            return None, findings

        if year_dir != date_str[:4]:
            findings.append(
                Finding(
                    rel_path=norm,
                    line=1,
                    severity="error",
                    rule="invalid-path",
                    message=(
                        f"Year directory '{year_dir}' does not match filename "
                        f"date '{date_str}'."
                    ),
                )
            )
        return date_str, findings

    # parts[0] == "tc"
    # Expected either:
    #   tc/<domain>/<YYYY>/<YYYY-MM-DD>.md (len == 4)
    #   tc/<domain>/dwg/<wg>/<YYYY>/<YYYY-MM-DD>.md (len == 6)
    if len(parts) == 4:
        domain, year_dir, filename = parts[1], parts[2], parts[3]
        if domain not in VALID_DOMAINS:
            findings.append(
                Finding(
                    rel_path=norm,
                    line=1,
                    severity="error",
                    rule="invalid-path",
                    message=(
                        f"Unknown DTC domain '{domain}'; expected one of "
                        f"{sorted(VALID_DOMAINS)}."
                    ),
                )
            )
    elif len(parts) == 6 and parts[2] == "dwg":
        domain, wg_slug, year_dir, filename = (
            parts[1],
            parts[3],
            parts[4],
            parts[5],
        )
        if domain not in VALID_DOMAINS:
            findings.append(
                Finding(
                    rel_path=norm,
                    line=1,
                    severity="error",
                    rule="invalid-path",
                    message=(
                        f"Unknown sponsoring DTC domain '{domain}'; expected "
                        f"one of {sorted(VALID_DOMAINS)}."
                    ),
                )
            )
        if not WG_SLUG_RE.match(wg_slug):
            findings.append(
                Finding(
                    rel_path=norm,
                    line=1,
                    severity="error",
                    rule="invalid-path",
                    message=(
                        f"Invalid DWG directory name '{wg_slug}'; expected "
                        "lowercase kebab-case."
                    ),
                )
            )
    else:
        findings.append(
            Finding(
                rel_path=norm,
                line=1,
                severity="error",
                rule="invalid-path",
                message=(
                    "TC minutes must be placed at "
                    "'tc/<domain>/<YYYY>/<YYYY-MM-DD>.md' or "
                    "'tc/<domain>/dwg/<wg>/<YYYY>/<YYYY-MM-DD>.md' "
                    f"(got '{norm}')."
                ),
            )
        )
        return None, findings

    m = TC_FILENAME_RE.match(filename)
    if not m:
        findings.append(
            Finding(
                rel_path=norm,
                line=1,
                severity="error",
                rule="invalid-path",
                message=(
                    f"TC/DWG minutes filename must match 'YYYY-MM-DD.md' "
                    f"(got '{filename}')."
                ),
            )
        )
        return None, findings

    date_str = m.group(1)
    if not _parse_iso_date(date_str):
        findings.append(
            Finding(
                rel_path=norm,
                line=1,
                severity="error",
                rule="invalid-date",
                message=f"Filename contains invalid calendar date '{date_str}'.",
            )
        )
        return None, findings

    if year_dir != date_str[:4]:
        findings.append(
            Finding(
                rel_path=norm,
                line=1,
                severity="error",
                rule="invalid-path",
                message=(
                    f"Year directory '{year_dir}' does not match filename "
                    f"date '{date_str}'."
                ),
            )
        )

    return date_str, findings


@dataclass
class _TopicBlock:
    """Tracks an active `- **Topic:**` block during validation."""

    title: str
    line: int
    indent: int
    has_details: bool = False
    linked_numbers: set[str] | None = None
    prose_mentions: list[tuple[str, int]] | None = None

    def __post_init__(self) -> None:
        if self.linked_numbers is None:
            self.linked_numbers = set()
        if self.prose_mentions is None:
            self.prose_mentions = []


def _flush_topic_warnings(
    topic: _TopicBlock | None,
    rel_path: str,
    findings: list[Finding],
) -> None:
    if topic is None:
        return

    if not topic.has_details:
        findings.append(
            Finding(
                rel_path=rel_path,
                line=topic.line,
                severity="warning",
                rule="empty-topic-block",
                message=(
                    f"Topic '{topic.title}' has no indented notes, discussion, "
                    "decision, or context sub-bullets."
                ),
            )
        )

    linked = topic.linked_numbers or set()
    seen_unlinked: set[str] = set()
    for num_str, line_no in topic.prose_mentions or []:
        if num_str not in linked and num_str not in seen_unlinked:
            seen_unlinked.add(num_str)
            findings.append(
                Finding(
                    rel_path=rel_path,
                    line=line_no,
                    severity="warning",
                    rule="unlinked-pr-or-issue",
                    message=(
                        f"Topic '{topic.title}' mentions #{num_str} without a "
                        "full GitHub link; add a link so Circle-Back automation "
                        "can reference it."
                    ),
                )
            )


def validate_minutes_markdown(content: str, rel_path: str) -> list[Finding]:
    """Validates a single meeting minutes Markdown file and returns findings."""
    norm = rel_path.strip().lstrip("./")
    expected_date, findings = validate_path_and_extract_date(norm)

    in_fence = False
    h1_line: int | None = None
    h1_text: str | None = None
    has_led_by = False
    has_attendance = False
    agenda_sections: list[str] = []
    active_topic: _TopicBlock | None = None

    for idx, line in enumerate(content.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        for ph_match in TEMPLATE_PLACEHOLDER_RE.finditer(line):
            findings.append(
                Finding(
                    rel_path=norm,
                    line=idx,
                    severity="error",
                    rule="unfilled-template-placeholder",
                    message=(
                        f"Unfilled template placeholder '{ph_match.group(0)}' found."
                    ),
                )
            )

        if DUMMY_LINK_RE.search(line):
            findings.append(
                Finding(
                    rel_path=norm,
                    line=idx,
                    severity="error",
                    rule="unfilled-template-placeholder",
                    message="Placeholder link '](#)' must be replaced or removed.",
                )
            )

        h1_match = H1_RE.match(line)
        if h1_match and h1_line is None:
            h1_line = idx
            h1_text = h1_match.group(1).strip()
            continue

        if LED_BY_RE.match(line):
            has_led_by = True

        h2_match = H2_RE.match(line)
        if h2_match:
            _flush_topic_warnings(active_topic, norm, findings)
            active_topic = None
            sec_title = h2_match.group(1).strip()
            if sec_title.lower() == "attendance":
                has_attendance = True
            else:
                agenda_sections.append(sec_title)
            continue

        if EMPTY_TOPIC_RE.match(line):
            _flush_topic_warnings(active_topic, norm, findings)
            active_topic = None
            findings.append(
                Finding(
                    rel_path=norm,
                    line=idx,
                    severity="error",
                    rule="malformed-topic-marker",
                    message=("Topic entry '- **Topic:**' is missing a topic title."),
                )
            )
            continue

        topic_match = CANONICAL_TOPIC_RE.match(line)
        if topic_match:
            _flush_topic_warnings(active_topic, norm, findings)
            indent = len(topic_match.group(1))
            title = topic_match.group(2).strip()
            active_topic = _TopicBlock(title=title, line=idx, indent=indent)
            for link_m in GH_ITEM_LINK_RE.finditer(line):
                assert active_topic.linked_numbers is not None
                active_topic.linked_numbers.add(link_m.group(1))
            for prose_m in PROSE_ITEM_MENTION_RE.finditer(line):
                assert active_topic.prose_mentions is not None
                active_topic.prose_mentions.append((prose_m.group(1), idx))
            continue

        if MALFORMED_TOPIC_RE.match(line):
            _flush_topic_warnings(active_topic, norm, findings)
            active_topic = None
            findings.append(
                Finding(
                    rel_path=norm,
                    line=idx,
                    severity="error",
                    rule="malformed-topic-marker",
                    message=(
                        "Non-canonical topic marker; use '- **Topic:** <Title>' "
                        "so automated tooling can parse it."
                    ),
                )
            )
            continue

        if active_topic is not None:
            stripped = line.strip()
            if not stripped:
                continue
            current_indent = len(line) - len(line.lstrip())
            list_match = LIST_ITEM_RE.match(line)
            if list_match and len(list_match.group(1)) <= active_topic.indent:
                _flush_topic_warnings(active_topic, norm, findings)
                active_topic = None
                continue
            if current_indent > active_topic.indent:
                active_topic.has_details = True
                for link_m in GH_ITEM_LINK_RE.finditer(line):
                    assert active_topic.linked_numbers is not None
                    active_topic.linked_numbers.add(link_m.group(1))
                for prose_m in PROSE_ITEM_MENTION_RE.finditer(line):
                    assert active_topic.prose_mentions is not None
                    active_topic.prose_mentions.append((prose_m.group(1), idx))

    _flush_topic_warnings(active_topic, norm, findings)

    if h1_line is None or h1_text is None:
        findings.append(
            Finding(
                rel_path=norm,
                line=1,
                severity="error",
                rule="missing-h1-title",
                message=(
                    "Missing top-level '# UCP ...' H1 heading at start of document."
                ),
            )
        )
    elif expected_date is not None and expected_date not in h1_text:
        dates_in_h1 = DATE_IN_TEXT_RE.findall(h1_text)
        found_hint = (
            f" (found '{dates_in_h1[0]}')" if dates_in_h1 else " (no date found)"
        )
        findings.append(
            Finding(
                rel_path=norm,
                line=h1_line,
                severity="error",
                rule="title-date-mismatch",
                message=(
                    f"H1 title date{found_hint} does not match filename date "
                    f"'{expected_date}'."
                ),
            )
        )

    if not has_led_by:
        findings.append(
            Finding(
                rel_path=norm,
                line=1,
                severity="error",
                rule="missing-led-by",
                message="Missing required '**Led by:** <Name>' metadata line.",
            )
        )

    if not has_attendance:
        findings.append(
            Finding(
                rel_path=norm,
                line=1,
                severity="error",
                rule="missing-attendance",
                message="Missing required '## Attendance' section.",
            )
        )

    if not agenda_sections:
        findings.append(
            Finding(
                rel_path=norm,
                line=1,
                severity="error",
                rule="missing-agenda-section",
                message=(
                    "Missing agenda sections; expected at least one '## ' "
                    "section in addition to '## Attendance'."
                ),
            )
        )

    return findings


def _escape_table_cell(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").replace("|", r"\|")


def render_markdown_summary(
    validated_files: list[str],
    findings: list[Finding],
) -> str:
    """Renders a Markdown summary table for GitHub Actions job summaries."""
    lines = ["### 📋 Meeting Minutes Structure Validation", ""]
    if not validated_files:
        lines.append(
            "_No added or modified meeting minutes files (`tc/**` or `gc/**`) "
            "to validate._"
        )
        return "\n".join(lines)

    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]

    lines.append(
        f"Validated **{len(validated_files)}** file(s): "
        f"**{len(errors)}** error(s), **{len(warnings)}** warning(s)."
    )
    lines.append("")

    if not findings:
        for rel_path in validated_files:
            lines.append(f"- ✅ `{rel_path}` — All structural checks passed.")
        return "\n".join(lines)

    lines.extend(
        [
            "| File | Line | Severity | Rule | Message |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for f in findings:
        badge = "❌ Error" if f.severity == "error" else "⚠️ Warning"
        msg = _escape_table_cell(f.message)
        lines.append(f"| `{f.rel_path}` | {f.line} | {badge} | `{f.rule}` | {msg} |")
    return "\n".join(lines)


def git_changed_minutes_files(
    base_sha: str | None,
    repo_root: Path,
) -> list[str]:
    """Returns added or modified Markdown files under tc/ and gc/ since base_sha."""
    if not base_sha or base_sha == ZERO_SHA or not SHA_RE.match(base_sha):
        cmd = [
            "git",
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "--diff-filter=ACMR",
            "HEAD",
            "--",
            "tc/**/*.md",
            "gc/**/*.md",
        ]
    else:
        cmd = [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            base_sha,
            "HEAD",
            "--",
            "tc/**/*.md",
            "gc/**/*.md",
        ]

    proc = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning("git diff returned %d: %s", proc.returncode, proc.stderr.strip())
        return []

    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def resolve_github_event_files(
    env: dict[str, str],
    repo_root: Path,
) -> list[str]:
    """Resolves target minutes files from GitHub Actions environment variables."""
    event_name = env.get("EVENT_NAME") or env.get("GITHUB_EVENT_NAME") or ""
    if event_name == "workflow_dispatch":
        input_file = (env.get("INPUT_MINUTES_FILE") or "").strip()
        return [input_file] if input_file else []
    if event_name == "pull_request":
        return git_changed_minutes_files(env.get("BASE_SHA"), repo_root)
    if event_name == "push":
        return git_changed_minutes_files(env.get("BEFORE_SHA"), repo_root)
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate UCP meeting minutes Markdown structure."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help=(
            "Meeting minutes Markdown file paths (relative to repo root). "
            "If omitted in CI, resolved from GitHub event."
        ),
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help=(
            "Path to the meeting-minutes repository root (default: current directory)."
        ),
    )
    parser.add_argument(
        "--github-annotations",
        action="store_true",
        default=os.getenv("GITHUB_ACTIONS") == "true",
        help="Emit GitHub Actions ::error and ::warning workflow commands.",
    )
    parser.add_argument(
        "--summary-file",
        default=os.getenv("GITHUB_STEP_SUMMARY"),
        help=(
            "Optional file path to write the Markdown summary report "
            "(defaults to GITHUB_STEP_SUMMARY if set)."
        ),
    )

    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    target_files = list(args.files)
    if not target_files and (os.getenv("EVENT_NAME") or os.getenv("GITHUB_EVENT_NAME")):
        target_files = resolve_github_event_files(dict(os.environ), repo_root)

    validated_files: list[str] = []
    all_findings: list[Finding] = []

    for file_arg in target_files:
        file_path = Path(file_arg)
        if not file_path.is_absolute():
            file_path = (repo_root / file_path).resolve()
        try:
            rel_path = file_path.relative_to(repo_root).as_posix()
        except ValueError:
            rel_path = Path(file_arg).as_posix()

        if not should_validate_file(rel_path):
            logger.info("Skipping non-minutes file: %s", rel_path)
            continue

        if not file_path.is_file():
            logger.warning("Skipping missing file: %s", rel_path)
            continue

        validated_files.append(rel_path)
        content = file_path.read_text(encoding="utf-8")
        findings = validate_minutes_markdown(content, rel_path=rel_path)
        all_findings.extend(findings)

        if not findings:
            logger.info("OK: %s", rel_path)
        else:
            for f in findings:
                logger.info(f.format_cli())
                if args.github_annotations:
                    print(f.format_github_annotation())

    if args.summary_file:
        summary_md = render_markdown_summary(validated_files, all_findings)
        with Path(args.summary_file).open("a", encoding="utf-8") as f:
            f.write(summary_md + "\n")

    has_errors = any(f.severity == "error" for f in all_findings)
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())

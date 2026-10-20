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

"""Circle-back tool for UCP meeting minutes.

Parses published meeting minutes Markdown files, extracts PRs and Issues
referenced under agenda topics, checks their live state on GitHub, and posts
idempotent link-back comments on open PRs/Issues.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("circle_back")

DEFAULT_ORG = "Universal-Commerce-Protocol"
DEFAULT_MINUTES_REPO = "meeting-minutes"
DEFAULT_BRANCH = "main"

MINUTES_FILENAME_RE = re.compile(r"^(?:GC-)?\d{4}-\d{2}-\d{2}\.md$")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
SECTION_HEADING_RE = re.compile(r"^#{2,6}\s+(.+?)\s*$")
TOPIC_RE = re.compile(
    r"^(\s*)[-*+]\s+(?:\*\*Topic:\*\*|\*\*Topic\*\*:|Topic:)\s*(.+?)\s*$"
)
LIST_ITEM_RE = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+")
OPEN_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
INLINE_CODE_RE = re.compile(r"`+[^`]+`+")
GH_LINK_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(pull|issues)/(\d+)(?![0-9])"
)
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
TRAILING_AUTOLINK_PAREN_RE = re.compile(r"\s*\(<?https?://[^\s>)]+>?\)\s*$")
INLINE_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(https?://[^)]+\)")
AUTOLINK_RE = re.compile(r"<https?://[^>]+>")

PREVIEW_COMMENT_MARKER = "<!-- ucp-meeting-minutes-preview -->"


@dataclass(frozen=True)
class TopicRef:
    """A reference to an agenda topic and its Markdown section anchor."""

    title: str
    section_slug: str = ""


@dataclass
class CircleBackTarget:
    """A GitHub PR or Issue referenced in a meeting minutes file."""

    org: str
    repo: str
    kind: str  # "pull" or "issues"
    number: int
    topics: list[TopicRef] = field(default_factory=list)

    @property
    def short_ref(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.org}/{self.repo}/{self.kind}/{self.number}"

    @property
    def item_noun(self) -> str:
        return "pull request" if self.kind == "pull" else "issue"

    def add_topic(self, title: str, section_slug: str) -> None:
        ref = TopicRef(title=title, section_slug=section_slug)
        if ref not in self.topics:
            self.topics.append(ref)


@dataclass
class ParsedMinutes:
    """Parsed representation of a single meeting minutes Markdown file."""

    rel_path: str
    meeting_title: str
    targets: list[CircleBackTarget] = field(default_factory=list)


@dataclass
class TargetResult:
    """Outcome of evaluating/processing a single CircleBackTarget."""

    target: CircleBackTarget
    state: str  # "open", "closed", "merged", "unknown"
    # "posted", "would_post", "skipped_closed", "skipped_merged",
    # "skipped_existing", "error"
    action: str
    comment_body: str
    detail: str = ""


def github_heading_slug(heading_text: str) -> str:
    """Computes a GitHub-compatible Markdown heading anchor slug."""
    slug = heading_text.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    return slug.replace(" ", "-")


def _normalize_rel_path(rel_path: str) -> str:
    """Normalizes a repo-relative path without stripping leading dots on dotfiles."""
    norm = Path(rel_path.strip()).as_posix()
    while norm.startswith("./"):
        norm = norm.removeprefix("./")
    return norm.lstrip("/")


def is_meeting_minutes_file(rel_path: str) -> bool:
    """Returns True if rel_path is a dated TC/DWG/GC meeting minutes file."""
    norm = _normalize_rel_path(rel_path)
    parts = Path(norm).parts
    if len(parts) < 2 or parts[0] not in ("tc", "gc"):
        return False
    return bool(MINUTES_FILENAME_RE.match(parts[-1]))


def _strip_outer_bold(text: str) -> str:
    """Strips outer **...** only when wrapping a single bold span."""
    if (
        text.startswith("**")
        and text.endswith("**")
        and len(text) > 4
        and "**" not in text[2:-2]
    ):
        return text[2:-2].strip()
    return text


def _clean_topic_title(raw_title: str) -> str:
    """Strips redundant outer bold markers and inline URLs from a topic title."""
    cleaned = _strip_outer_bold(raw_title.strip())
    cleaned = INLINE_MD_LINK_RE.sub(r"\1", cleaned)
    cleaned = TRAILING_AUTOLINK_PAREN_RE.sub("", cleaned)
    cleaned = AUTOLINK_RE.sub("", cleaned).strip()
    cleaned = _strip_outer_bold(cleaned)
    return cleaned or raw_title.strip(" *<>")


def parse_minutes_markdown(
    content: str,
    rel_path: str,
    target_org: str = DEFAULT_ORG,
    minutes_repo: str = DEFAULT_MINUTES_REPO,
) -> ParsedMinutes:
    """Parses a meeting minutes Markdown string and extracts PR/Issue targets."""
    norm_path = _normalize_rel_path(rel_path)
    default_title = Path(norm_path).stem
    meeting_title = default_title
    current_section_slug = ""
    current_topic: str | None = None
    current_topic_indent: int | None = None
    fence_char: str | None = None
    fence_len: int = 0

    targets_by_key: dict[tuple[str, str, int], CircleBackTarget] = {}

    for line in content.splitlines():
        fence_match = OPEN_FENCE_RE.match(line)
        if fence_char is not None:
            if (
                fence_match
                and fence_match.group(1)[0] == fence_char
                and len(fence_match.group(1)) >= fence_len
                and not fence_match.group(2).strip()
            ):
                fence_char = None
                fence_len = 0
            continue
        if fence_match:
            fence_char = fence_match.group(1)[0]
            fence_len = len(fence_match.group(1))
            continue

        h1_match = H1_RE.match(line)
        if h1_match and meeting_title == default_title:
            meeting_title = h1_match.group(1).strip()
            continue

        section_match = SECTION_HEADING_RE.match(line)
        if section_match:
            heading_text = section_match.group(1).strip()
            if (
                meeting_title == default_title
                and not current_section_slug
                and heading_text.lower().startswith("ucp ")
            ):
                meeting_title = heading_text
            else:
                current_section_slug = github_heading_slug(heading_text)
            current_topic = None
            current_topic_indent = None
            continue

        topic_match = TOPIC_RE.match(line)
        if topic_match:
            current_topic_indent = len(topic_match.group(1))
            current_topic = _clean_topic_title(topic_match.group(2))
        else:
            list_match = LIST_ITEM_RE.match(line)
            if (
                list_match
                and current_topic_indent is not None
                and len(list_match.group(1)) <= current_topic_indent
            ):
                current_topic = None
                current_topic_indent = None

        if not current_topic:
            continue

        line_without_code = INLINE_CODE_RE.sub("", line)
        for match in GH_LINK_RE.finditer(line_without_code):
            org, repo, kind, num_str = match.groups()
            if org.lower() != target_org.lower():
                continue
            if repo.lower() == minutes_repo.lower():
                continue
            number = int(num_str)
            key = (org.lower(), repo.lower(), number)
            if key not in targets_by_key:
                targets_by_key[key] = CircleBackTarget(
                    org=org,
                    repo=repo,
                    kind=kind,
                    number=number,
                )
            elif kind == "pull" and targets_by_key[key].kind == "issues":
                targets_by_key[key].kind = "pull"
            targets_by_key[key].add_topic(current_topic, current_section_slug)

    return ParsedMinutes(
        rel_path=norm_path,
        meeting_title=meeting_title,
        targets=list(targets_by_key.values()),
    )


def comment_marker(rel_path: str) -> str:
    """Returns the hidden HTML marker used to ensure idempotency per minutes file."""
    norm_path = _normalize_rel_path(rel_path)
    return f"<!-- ucp-meeting-minutes: {norm_path} -->"


def format_comment(
    parsed: ParsedMinutes,
    target: CircleBackTarget,
    org: str = DEFAULT_ORG,
    minutes_repo: str = DEFAULT_MINUTES_REPO,
    branch: str = DEFAULT_BRANCH,
) -> str:
    """Formats the Option B topic-indexed link-back comment for a target PR/Issue."""
    marker = comment_marker(parsed.rel_path)
    minutes_url = (
        f"https://github.com/{org}/{minutes_repo}/blob/{branch}/{parsed.rel_path}"
    )

    lines = [
        marker,
        f"📋 **Discussed in [{parsed.meeting_title}]({minutes_url})**",
        "",
        f"This {target.item_noun} was referenced under the following agenda topic(s):",
    ]
    for topic in target.topics:
        if topic.section_slug:
            section_url = f"{minutes_url}#{topic.section_slug}"
            lines.append(f"* **{topic.title}** ([view section]({section_url}))")
        else:
            lines.append(f"* **{topic.title}**")

    lines.extend(
        [
            "",
            (
                f"_See the full [meeting minutes]({minutes_url}) "
                "for discussion notes and action items._"
            ),
        ]
    )
    return "\n".join(lines)


class GitHubApiClient:
    """Lightweight GitHub REST API client using Python's standard library."""

    def __init__(
        self, token: str | None = None, api_url: str = "https://api.github.com"
    ):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self._state_cache: dict[tuple[str, str, int], tuple[str, str]] = {}

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
    ) -> object:
        url = f"{self.api_url}/{path.lstrip('/')}"
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"Unsupported URL scheme: {url}")
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ucp-meeting-minutes-circle-back",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(  # noqa: S310
            url, data=data, headers=headers, method=method
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}

    def get_target_state(self, target: CircleBackTarget) -> str:
        """Returns 'open', 'merged', or 'closed' for a PR or Issue."""
        cache_key = (
            target.org.lower(),
            target.repo.lower(),
            target.number,
        )
        if cache_key in self._state_cache:
            state, resolved_kind = self._state_cache[cache_key]
            target.kind = resolved_kind
            return state

        endpoint = "pulls" if target.kind == "pull" else "issues"
        data = self._request(
            "GET", f"/repos/{target.org}/{target.repo}/{endpoint}/{target.number}"
        )
        resolved_kind = target.kind
        if not isinstance(data, dict):
            state = "unknown"
        elif target.kind == "pull" and data.get("merged"):
            state = "merged"
        elif isinstance(data.get("pull_request"), dict):
            resolved_kind = "pull"
            target.kind = "pull"
            if data["pull_request"].get("merged_at"):
                state = "merged"
            else:
                state = str(data.get("state", "unknown"))
        else:
            state = str(data.get("state", "unknown"))

        self._state_cache[cache_key] = (state, resolved_kind)
        return state

    def has_comment_with_marker(self, target: CircleBackTarget, marker: str) -> bool:
        """Checks whether the target PR/Issue already has a marker comment."""
        page = 1
        while True:
            data = self._request(
                "GET",
                f"/repos/{target.org}/{target.repo}/issues/{target.number}/comments?per_page=100&page={page}",
            )
            if not isinstance(data, list) or not data:
                return False
            for comment in data:
                body = comment.get("body") or ""
                if marker in body:
                    return True
            if len(data) < 100:
                return False
            page += 1

    def post_comment(self, target: CircleBackTarget, body: str) -> None:
        """Posts a new comment on the target PR/Issue."""
        self._request(
            "POST",
            f"/repos/{target.org}/{target.repo}/issues/{target.number}/comments",
            payload={"body": body},
        )

    def upsert_pr_preview_comment(
        self, org: str, repo: str, pr_number: int, body: str
    ) -> None:
        """Creates or updates the sticky preview comment on a meeting-minutes PR."""
        page = 1
        existing_comment_id: int | None = None
        while True:
            data = self._request(
                "GET",
                f"/repos/{org}/{repo}/issues/{pr_number}/comments?per_page=100&page={page}",
            )
            if not isinstance(data, list) or not data:
                break
            for comment in data:
                c_body = comment.get("body") or ""
                if PREVIEW_COMMENT_MARKER in c_body:
                    existing_comment_id = comment.get("id")
                    break
            if existing_comment_id is not None or len(data) < 100:
                break
            page += 1

        if existing_comment_id is not None:
            self._request(
                "PATCH",
                f"/repos/{org}/{repo}/issues/comments/{existing_comment_id}",
                payload={"body": body},
            )
        else:
            self._request(
                "POST",
                f"/repos/{org}/{repo}/issues/{pr_number}/comments",
                payload={"body": body},
            )


def process_minutes(
    parsed: ParsedMinutes,
    client: GitHubApiClient,
    apply: bool = False,
    org: str = DEFAULT_ORG,
    minutes_repo: str = DEFAULT_MINUTES_REPO,
    branch: str = DEFAULT_BRANCH,
) -> list[TargetResult]:
    """Evaluates and optionally posts circle-back comments for a ParsedMinutes file."""
    results: list[TargetResult] = []
    marker = comment_marker(parsed.rel_path)

    logger.info(
        "\nProcessing %s (%d referenced PRs/Issues)",
        parsed.rel_path,
        len(parsed.targets),
    )

    for target in parsed.targets:
        try:
            state = client.get_target_state(target)
        except (
            urllib.error.URLError,
            json.JSONDecodeError,
            ValueError,
            OSError,
        ) as exc:
            logger.error("  Error fetching %s: %s", target.short_ref, exc)
            body = format_comment(
                parsed,
                target,
                org=org,
                minutes_repo=minutes_repo,
                branch=branch,
            )
            results.append(
                TargetResult(
                    target=target,
                    state="unknown",
                    action="error",
                    comment_body=body,
                    detail=str(exc),
                )
            )
            continue

        body = format_comment(
            parsed,
            target,
            org=org,
            minutes_repo=minutes_repo,
            branch=branch,
        )

        if state == "merged":
            logger.info("  Skipping %s: PR is already merged.", target.short_ref)
            results.append(
                TargetResult(
                    target=target,
                    state=state,
                    action="skipped_merged",
                    comment_body=body,
                )
            )
            continue

        if state != "open":
            logger.info(
                "  Skipping %s: %s is %s.",
                target.short_ref,
                target.item_noun,
                state,
            )
            results.append(
                TargetResult(
                    target=target,
                    state=state,
                    action="skipped_closed",
                    comment_body=body,
                )
            )
            continue

        try:
            already_commented = client.has_comment_with_marker(target, marker)
        except (
            urllib.error.URLError,
            json.JSONDecodeError,
            ValueError,
            OSError,
        ) as exc:
            logger.error("  Error checking comments on %s: %s", target.short_ref, exc)
            results.append(
                TargetResult(
                    target=target,
                    state=state,
                    action="error",
                    comment_body=body,
                    detail=str(exc),
                )
            )
            continue

        if already_commented:
            logger.info(
                "  Skipping %s: Circle-back comment already exists.", target.short_ref
            )
            results.append(
                TargetResult(
                    target=target,
                    state=state,
                    action="skipped_existing",
                    comment_body=body,
                )
            )
            continue

        if apply:
            try:
                client.post_comment(target, body)
                logger.info("  Posted circle-back comment on %s.", target.short_ref)
                results.append(
                    TargetResult(
                        target=target,
                        state=state,
                        action="posted",
                        comment_body=body,
                    )
                )
            except (
                urllib.error.URLError,
                json.JSONDecodeError,
                ValueError,
                OSError,
            ) as exc:
                logger.error("  Error posting comment on %s: %s", target.short_ref, exc)
                results.append(
                    TargetResult(
                        target=target,
                        state=state,
                        action="error",
                        comment_body=body,
                        detail=str(exc),
                    )
                )
        else:
            logger.info(
                "  [DRY RUN] Would post circle-back comment on %s.", target.short_ref
            )
            results.append(
                TargetResult(
                    target=target,
                    state=state,
                    action="would_post",
                    comment_body=body,
                )
            )

    return results


def _escape_table_cell(text: str) -> str:
    """Escapes pipes and newlines for safe Markdown table cell embedding."""
    return text.replace("\r", " ").replace("\n", " ").replace("|", r"\|")


def render_markdown_summary(
    all_results: list[tuple[ParsedMinutes, list[TargetResult]]],
    apply: bool,
    org: str = DEFAULT_ORG,
    minutes_repo: str = DEFAULT_MINUTES_REPO,
    branch: str = DEFAULT_BRANCH,
) -> str:
    """Renders a Markdown summary table for PR comments or Job Summaries."""
    mode_title = (
        "🔔 Meeting Minutes Circle-Back Report"
        if apply
        else "🔔 Meeting Minutes Circle-Back Preview"
    )
    lines = [PREVIEW_COMMENT_MARKER, f"### {mode_title}", ""]

    if not all_results:
        lines.append("_No meeting minutes files (`tc/**` or `gc/**`) were modified._")
        return "\n".join(lines)

    state_badges = {
        "open": "🟢 Open",
        "merged": "🟣 Merged",
        "closed": "🔴 Closed",
        "unknown": "⚪ Unknown",
    }
    action_labels = {
        "posted": "✅ Posted comment",
        "would_post": "📝 Will post comment on merge",
        "skipped_merged": "⏭️ Skipped (merged)",
        "skipped_closed": "⏭️ Skipped (closed)",
        "skipped_existing": "⏭️ Skipped (already commented)",
        "error": "❌ Error",
    }

    for parsed, results in all_results:
        minutes_url = (
            f"https://github.com/{org}/{minutes_repo}/blob/{branch}/{parsed.rel_path}"
        )
        lines.append(f"#### 📄 [`{parsed.rel_path}`]({minutes_url})")
        if not results:
            lines.append("_No PRs or Issues referenced under agenda topics._")
            lines.append("")
            continue

        lines.extend(
            [
                "",
                "| Target | State | Referenced Under Topic(s) | Action |",
                "| :--- | :--- | :--- | :--- |",
            ]
        )
        for res in results:
            t = res.target
            topics_str = "<br>".join(
                f"• *{_escape_table_cell(topic.title)}*" for topic in t.topics
            )
            state_str = state_badges.get(res.state, res.state)
            action_str = action_labels.get(res.action, res.action)
            if res.detail:
                action_str = f"{action_str} (`{_escape_table_cell(res.detail)}`)"
            lines.append(
                f"| [`{t.short_ref}`]({t.html_url}) | {state_str} "
                f"| {topics_str} | {action_str} |"
            )

        lines.append("")

        preview_targets = [r for r in results if r.action in ("would_post", "posted")]
        if preview_targets:
            lines.extend(
                [
                    "<details>",
                    (
                        "<summary>Preview circle-back comments "
                        f"({len(preview_targets)})</summary>"
                    ),
                    "",
                ]
            )
            for res in preview_targets:
                t = res.target
                lines.extend(
                    [
                        f"**[`{t.short_ref}`]({t.html_url})**:",
                        "",
                        "```markdown",
                        res.comment_body,
                        "```",
                        "",
                    ]
                )
            lines.extend(["</details>", ""])

    return "\n".join(lines)


ZERO_SHA = "0000000000000000000000000000000000000000"


def git_changed_minutes_files(
    base_sha: str | None,
    repo_root: Path,
) -> list[str]:
    """Returns added or modified Markdown files under tc/ and gc/ since base_sha."""
    if not base_sha or base_sha == ZERO_SHA or not SHA_RE.match(base_sha):
        cmd = [
            "git",
            "diff-tree",
            "-m",
            "--first-parent",
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


def resolve_github_event_config(
    env: dict[str, str],
    repo_root: Path,
) -> tuple[list[str], bool, int | None]:
    """Resolves target files, apply flag, and preview PR from CI env vars."""
    event_name = env.get("EVENT_NAME") or env.get("GITHUB_EVENT_NAME") or ""
    if event_name == "workflow_dispatch":
        input_file = (env.get("INPUT_MINUTES_FILE") or "").strip()
        files = [input_file] if input_file else []
        apply = (env.get("INPUT_APPLY") or "").strip().lower() == "true"
        return files, apply, None

    if event_name == "pull_request":
        files = git_changed_minutes_files(env.get("BASE_SHA"), repo_root)
        pr_str = (env.get("PR_NUMBER") or "").strip()
        preview_pr = int(pr_str) if pr_str.isdigit() else None
        return files, False, preview_pr

    if event_name == "push":
        files = git_changed_minutes_files(env.get("BEFORE_SHA"), repo_root)
        return files, True, None

    return [], False, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Post or preview circle-back comments from UCP meeting minutes."
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
        "--org",
        default=os.getenv("ORG_OWNER") or DEFAULT_ORG,
        help=f"GitHub organization (default: {DEFAULT_ORG}).",
    )
    parser.add_argument(
        "--minutes-repo",
        default=DEFAULT_MINUTES_REPO,
        help=f"Meeting minutes repository name (default: {DEFAULT_MINUTES_REPO}).",
    )
    parser.add_argument(
        "--branch",
        default=DEFAULT_BRANCH,
        help=f"Target branch for minutes links (default: {DEFAULT_BRANCH}).",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ORG_TRIAGE_TOKEN") or os.getenv("GITHUB_TOKEN"),
        help="GitHub token (defaults to ORG_TRIAGE_TOKEN or GITHUB_TOKEN env var).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually post comments to open PRs/Issues (default is dry-run).",
    )
    parser.add_argument(
        "--summary-file",
        default=os.getenv("GITHUB_STEP_SUMMARY"),
        help=(
            "Optional file path to write the Markdown summary report "
            "(defaults to GITHUB_STEP_SUMMARY if set)."
        ),
    )
    parser.add_argument(
        "--preview-pr",
        type=int,
        help=(
            "Optional PR number in the meeting-minutes repo on which "
            "to post/update the preview comment."
        ),
    )

    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    target_files = list(args.files)
    apply = args.apply
    preview_pr = args.preview_pr

    if not target_files and (os.getenv("EVENT_NAME") or os.getenv("GITHUB_EVENT_NAME")):
        target_files, env_apply, env_preview_pr = resolve_github_event_config(
            dict(os.environ), repo_root
        )
        apply = apply or env_apply
        if preview_pr is None:
            preview_pr = env_preview_pr

    if not target_files:
        logger.info("No added or modified meeting minutes files detected.")
        return 0

    api_url = os.getenv("GITHUB_API_URL", "https://api.github.com")
    client = GitHubApiClient(token=args.token, api_url=api_url)

    all_results: list[tuple[ParsedMinutes, list[TargetResult]]] = []
    has_errors = False

    for file_arg in target_files:
        file_path = Path(file_arg)
        if not file_path.is_absolute():
            file_path = (repo_root / file_path).resolve()
        try:
            rel_path = file_path.relative_to(repo_root).as_posix()
        except ValueError:
            logger.warning("Skipping path outside repository root: %s", file_arg)
            continue

        if not is_meeting_minutes_file(rel_path):
            logger.info("Skipping non-minutes file: %s", rel_path)
            continue

        if not file_path.is_file():
            logger.warning("Skipping missing file: %s", rel_path)
            continue

        content = file_path.read_text(encoding="utf-8")
        parsed = parse_minutes_markdown(
            content,
            rel_path=rel_path,
            target_org=args.org,
            minutes_repo=args.minutes_repo,
        )
        results = process_minutes(
            parsed,
            client=client,
            apply=apply,
            org=args.org,
            minutes_repo=args.minutes_repo,
            branch=args.branch,
        )
        if any(r.action == "error" for r in results):
            has_errors = True
        all_results.append((parsed, results))

    summary_md = render_markdown_summary(
        all_results,
        apply=apply,
        org=args.org,
        minutes_repo=args.minutes_repo,
        branch=args.branch,
    )

    if args.summary_file:
        with Path(args.summary_file).open("a", encoding="utf-8") as f:
            f.write(summary_md + "\n")

    if preview_pr and all_results:
        try:
            client.upsert_pr_preview_comment(
                org=args.org,
                repo=args.minutes_repo,
                pr_number=preview_pr,
                body=summary_md,
            )
            logger.info(
                "Updated preview comment on %s/%s#%d.",
                args.org,
                args.minutes_repo,
                preview_pr,
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                logger.warning(
                    "Note: Could not post preview comment on PR #%d (HTTP %d); "
                    "job summary still generated.",
                    preview_pr,
                    exc.code,
                )
            else:
                logger.error(
                    "Failed to post preview comment on PR #%d: %s",
                    preview_pr,
                    exc,
                )
                has_errors = True
        except urllib.error.URLError as exc:
            logger.error(
                "Failed to post preview comment on PR #%d: %s",
                preview_pr,
                exc,
            )
            has_errors = True

    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())

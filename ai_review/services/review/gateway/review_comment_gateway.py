import re

from ai_review.config import settings
from ai_review.libs.asynchronous.gather import bounded_gather
from ai_review.libs.logger import get_logger

from ai_review.services.artifacts.types import ArtifactsServiceProtocol
from ai_review.services.hook import hook
from ai_review.services.review.gateway.types import ReviewCommentGatewayProtocol
from ai_review.services.review.internal.inline.schema import InlineCommentListSchema, InlineCommentSchema
from ai_review.services.review.internal.inline_reply.schema import InlineCommentReplySchema
from ai_review.services.review.internal.summary.schema import SummaryCommentSchema
from ai_review.services.review.internal.summary_reply.schema import SummaryCommentReplySchema
from ai_review.services.vcs.types import VCSClientProtocol, ReviewThreadSchema, ReviewCommentSchema

logger = get_logger("REVIEW_COMMENT_GATEWAY")


class ReviewCommentGateway(ReviewCommentGatewayProtocol):
    def __init__(self, vcs: VCSClientProtocol, artifacts: ArtifactsServiceProtocol):
        self.vcs = vcs
        self.artifacts = artifacts
        self.created_inline_comments: list[InlineCommentSchema] = []

    async def get_inline_threads(self) -> list[ReviewThreadSchema]:
        threads = await self.vcs.get_inline_threads()
        inline_threads = [
            thread for thread in threads
            if any(settings.review.inline_reply_tag in comment.body for comment in thread.comments)
        ]
        logger.info(f"Detected {len(inline_threads)}/{len(threads)} AI inline threads")
        return inline_threads

    async def get_summary_threads(self) -> list[ReviewThreadSchema]:
        threads = await self.vcs.get_general_threads()
        summary_threads = [
            thread for thread in threads
            if any(settings.review.summary_reply_tag in comment.body for comment in thread.comments)
        ]
        logger.info(f"Detected {len(summary_threads)}/{len(threads)} AI summary threads")
        return summary_threads

    async def get_inline_comments(self) -> list[ReviewCommentSchema]:
        comments = await self.vcs.get_inline_comments()
        inline_comments = [
            comment for comment in comments
            if settings.review.inline_tag in comment.body
        ]
        logger.info(f"Detected {len(inline_comments)}/{len(comments)} AI inline comments")
        return inline_comments

    async def get_summary_comments(self) -> list[ReviewCommentSchema]:
        comments = await self.vcs.get_general_comments()
        summary_comments = [
            comment for comment in comments
            if settings.review.summary_tag in comment.body
        ]
        logger.info(f"Detected {len(summary_comments)}/{len(comments)} AI summary comments")
        return summary_comments

    async def process_inline_reply(self, thread_id: str, reply: InlineCommentReplySchema) -> None:
        try:
            await hook.emit_inline_comment_reply_start(reply)
            await self.vcs.create_inline_reply(thread_id, reply.body_with_tag)
            await hook.emit_inline_comment_reply_complete(reply)

            await self.artifacts.save_vcs_inline_reply(thread_id, reply)
        except Exception as error:
            logger.exception(f"Failed to create inline reply for thread {thread_id}: {error}")
            await hook.emit_inline_comment_reply_error(reply)

    async def process_summary_reply(self, thread_id: str, reply: SummaryCommentReplySchema) -> None:
        try:
            await hook.emit_summary_comment_reply_start(reply)
            await self.vcs.create_summary_reply(thread_id, reply.body_with_tag)
            await hook.emit_summary_comment_reply_complete(reply)

            await self.artifacts.save_vcs_summary_reply(thread_id, reply)
        except Exception as error:
            logger.exception(f"Failed to create summary reply for thread {thread_id}: {error}")
            await hook.emit_summary_comment_reply_error(reply)

    async def process_inline_comment(self, comment: InlineCommentSchema) -> None:
        try:
            await hook.emit_inline_comment_start(comment)
            await self.vcs.create_inline_comment(
                file=comment.file,
                line=comment.line,
                message=comment.body_with_tag,
            )
            self.created_inline_comments.append(comment)
            await hook.emit_inline_comment_complete(comment)

            await self.artifacts.save_vcs_inline(comment)
        except Exception as error:
            logger.exception(
                f"Failed to process inline comment for {comment.file}:{comment.line} — {error}"
            )
            await hook.emit_inline_comment_error(comment)

            if settings.review.inline_comment_fallback:
                logger.warning(f"Falling back to general comment for {comment.file}:{comment.line}")
                await self.process_inline_fallback_comment(SummaryCommentSchema(text=comment.fallback_body))

            # Always track the processed inline comment as a finding, even on failure
            self.created_inline_comments.append(comment)



    async def process_inline_fallback_comment(self, comment: SummaryCommentSchema) -> None:
        try:
            await hook.emit_summary_comment_start(comment)
            await self.vcs.create_general_comment(comment.body_with_fallback_tag)
            await hook.emit_summary_comment_complete(comment)

            await self.artifacts.save_vcs_summary(comment)
        except Exception as error:
            logger.exception(f"Failed to process inline fallback comment: {comment} — {error}")
            await hook.emit_summary_comment_error(comment)

    # Keep history compact: only the newest N prior snapshots are retained.
    _SUMMARY_HISTORY_LIMIT = 5
    _SUMMARY_HISTORY_SEPARATOR = "<!-- ai-review-history-separator -->"
    _SUMMARY_HISTORY_NOTICE = (
        "Previous reviews are collapsed below (stale / resolved). "
        "The summary above is authoritative."
    )

    @staticmethod
    def _authoritative_summary_section(body: str) -> str:
        """Return only the current/latest section of a summary body."""
        tag = settings.review.summary_tag
        text = body.replace(tag, "").strip()
        separator = ReviewCommentGateway._SUMMARY_HISTORY_SEPARATOR
        if separator in text:
            text = text.split(separator, 1)[0].strip()
        # Drop leftover notices from older stacking formats.
        text = re.sub(
            r"(?im)^(?:Current summary above is authoritative\..*|Previous reviews are collapsed below.*)$\n*",
            "",
            text,
        ).strip()
        return text

    @staticmethod
    def _snapshot_status_line(content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:160]
        return "Previous review"

    @staticmethod
    def _snapshot_sha(content: str) -> str | None:
        match = re.search(r"\[([0-9a-f]{7,40})\]\(", content)
        return match.group(1)[:7] if match else None

    @staticmethod
    def _parse_history_snapshots(history: str) -> list[str]:
        """Extract discrete prior snapshots from stacked history text."""
        if not history.strip():
            return []

        snapshots: list[str] = []

        # Prefer already-collapsed <details> blocks (current format).
        details_blocks = re.findall(
            r"<details>\s*<summary>.*?</summary>\s*(.*?)\s*</details>",
            history,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if details_blocks:
            for block in details_blocks:
                cleaned = ReviewCommentGateway._authoritative_summary_section(block)
                # Nested parent wrapper may have only child details; skip empties.
                if cleaned and not cleaned.lower().startswith("<details"):
                    snapshots.append(cleaned)
            if snapshots:
                return snapshots

        # Legacy: "### Previous review ..." sections (possibly repeated).
        sections = re.split(r"(?m)^### Previous review[^\n]*\n", history)
        for section in sections:
            cleaned = ReviewCommentGateway._authoritative_summary_section(section)
            cleaned = re.sub(
                r"(?im)^(?:Current summary above is authoritative\..*|Previous reviews are collapsed below.*)$\n*",
                "",
                cleaned,
            ).strip()
            if cleaned:
                snapshots.append(cleaned)

        return snapshots

    def _format_history_snapshot(self, content: str) -> str:
        status = self._snapshot_status_line(content)
        sha = self._snapshot_sha(content)
        if sha:
            label = f"Previous review ({sha}) — {status}"
        else:
            label = f"Previous review — {status}"
        # Gitea renders HTML details/summary as collapsible sections.
        return (
            f"<details>\n"
            f"<summary>{label}</summary>\n\n"
            f"{content.strip()}\n\n"
            f"</details>"
        )

    def build_summary_body_with_history(self, new_text: str, old_body: str) -> str:
        """Stack the new summary on top; hide prior runs in collapsible blocks."""
        tag = settings.review.summary_tag
        separator = self._SUMMARY_HISTORY_SEPARATOR
        old_body_clean = old_body.replace(tag, "").strip()

        if separator in old_body_clean:
            parts = old_body_clean.split(separator, 1)
            prev_latest = self._authoritative_summary_section(parts[0])
            prev_history = parts[1].strip() if len(parts) > 1 else ""
        else:
            prev_latest = self._authoritative_summary_section(old_body_clean)
            prev_history = ""

        snapshots: list[str] = []
        if prev_latest:
            snapshots.append(prev_latest)
        snapshots.extend(self._parse_history_snapshots(prev_history))

        # De-dupe identical consecutive snapshots (re-runs on same text).
        deduped: list[str] = []
        for snap in snapshots:
            if not deduped or deduped[-1] != snap:
                deduped.append(snap)
        snapshots = deduped[: self._SUMMARY_HISTORY_LIMIT]

        if not snapshots:
            return f"{new_text.strip()}\n\n{tag}"

        history_blocks = "\n\n".join(self._format_history_snapshot(s) for s in snapshots)
        return (
            f"{new_text.strip()}\n\n"
            f"{separator}\n\n"
            f"{self._SUMMARY_HISTORY_NOTICE}\n\n"
            f"{history_blocks}\n\n"
            f"{tag}"
        )


    async def process_summary_comment(self, comment: SummaryCommentSchema) -> None:
        try:
            await hook.emit_summary_comment_start(comment)

            existing_comments = await self.get_summary_comments()
            if existing_comments:
                existing_comment = existing_comments[0]
                new_body = self.build_summary_body_with_history(comment.text, existing_comment.body)
                await self.vcs.update_general_comment(existing_comment.id, new_body)
                logger.info(f"Updated existing summary comment {existing_comment.id}")
            else:
                await self.vcs.create_general_comment(comment.body_with_tag)
                logger.info("Created new summary comment")

            await hook.emit_summary_comment_complete(comment)
            await self.artifacts.save_vcs_summary(comment)
        except Exception as error:
            logger.exception(f"Failed to process summary comment: {comment} — {error}")
            await hook.emit_summary_comment_error(comment)

    async def process_inline_comments(self, comments: InlineCommentListSchema) -> None:
        await bounded_gather([self.process_inline_comment(comment) for comment in comments.root])

    async def clear_inline_comments(self) -> None:
        await hook.emit_clear_inline_comments_start()

        try:
            comments = await self.get_inline_comments()
            if not comments:
                logger.info("No AI inline comments to clear")
                await hook.emit_clear_inline_comments_complete(comments=comments)
                return

            # Gitea delete_inline_comment removes the whole review by review_id
            # (stored as comment.id). De-dupe so multi-comment reviews are only
            # deleted once.
            seen_ids: set[int | str] = set()
            to_delete: list[ReviewCommentSchema] = []
            for comment in comments:
                if comment.id in seen_ids:
                    continue
                seen_ids.add(comment.id)
                to_delete.append(comment)

            logger.info(
                f"Clearing {len(comments)} AI inline comments "
                f"({len(to_delete)} review delete(s))"
            )

            await bounded_gather([self.vcs.delete_inline_comment(comment.id) for comment in to_delete])
            await hook.emit_clear_inline_comments_complete(comments=comments)
        except Exception as error:
            logger.exception(f"Failed to clear inline comments: {error}")
            await hook.emit_clear_inline_comments_error()

    async def clear_summary_comments(self) -> None:
        await hook.emit_clear_summary_comments_start()

        try:
            comments = await self.get_summary_comments()
            if not comments:
                logger.info("No AI summary comments to clear")
                await hook.emit_clear_summary_comments_complete(comments=comments)
                return

            logger.info(f"Clearing {len(comments)} AI summary comments")

            await bounded_gather([self.vcs.delete_general_comment(comment.id) for comment in comments])
            await hook.emit_clear_summary_comments_complete(comments=comments)
        except Exception as error:
            logger.exception(f"Failed to clear summary comments: {error}")
            await hook.emit_clear_summary_comments_error()

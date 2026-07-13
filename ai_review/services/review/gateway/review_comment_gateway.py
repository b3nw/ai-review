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

    def build_summary_body_with_history(self, new_text: str, old_body: str) -> str:
        tag = settings.review.summary_tag
        old_body_clean = old_body.replace(tag, "").strip()

        separator = "<!-- ai-review-history-separator -->"
        notice = "Current summary above is authoritative. Previous snapshots are kept for context only."

        if separator in old_body_clean:
            parts = old_body_clean.split(separator, 1)
            prev_latest = parts[0].strip()
            prev_history = parts[1].strip() if len(parts) > 1 else ""
        else:
            prev_latest = old_body_clean
            prev_history = ""

        sha_match = re.search(r'\[([0-9a-f]{7})\]\(', prev_latest)
        if sha_match:
            prev_sha = sha_match.group(1)
            prev_header = f"### Previous review (commit {prev_sha})"
        else:
            prev_header = "### Previous review"

        history_block = f"\n\n{notice}\n\n{prev_header}\n{prev_latest}"
        if prev_history:
            history_block += f"\n\n{prev_history}"

        return f"{new_text}\n\n{separator}{history_block}\n\n{tag}"


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

            logger.info(f"Clearing {len(comments)} AI inline comments")

            await bounded_gather([self.vcs.delete_inline_comment(comment.id) for comment in comments])
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

from ai_review.config import settings
from ai_review.libs.logger import get_logger
from ai_review.services.cost.types import CostServiceProtocol

from ai_review.services.diff.types import DiffServiceProtocol
from ai_review.services.git.types import GitServiceProtocol
from ai_review.services.hook import hook
from ai_review.services.policy.types import PolicyServiceProtocol
from ai_review.services.prompt.adapter import build_prompt_context_from_review_info
from ai_review.services.prompt.types import PromptServiceProtocol
from ai_review.services.review.gateway.types import ReviewLLMGatewayProtocol, ReviewCommentGatewayProtocol
from ai_review.services.review.internal.summary.types import SummaryCommentServiceProtocol
from ai_review.services.review.runner.types import ReviewRunnerProtocol
from ai_review.services.vcs.types import VCSClientProtocol

logger = get_logger("SUMMARY_REVIEW_RUNNER")


class SummaryReviewRunner(ReviewRunnerProtocol):
    def __init__(
            self,
            vcs: VCSClientProtocol,
            git: GitServiceProtocol,
            diff: DiffServiceProtocol,
            cost: CostServiceProtocol,
            prompt: PromptServiceProtocol,
            policy: PolicyServiceProtocol,
            summary_comment: SummaryCommentServiceProtocol,
            review_llm_gateway: ReviewLLMGatewayProtocol,
            review_comment_gateway: ReviewCommentGatewayProtocol,
    ):
        self.vcs = vcs
        self.git = git
        self.diff = diff
        self.cost = cost
        self.prompt = prompt
        self.policy = policy
        self.summary_comment = summary_comment
        self.review_llm_gateway = review_llm_gateway
        self.review_comment_gateway = review_comment_gateway

    async def run(self) -> None:
        await hook.emit_summary_review_start()

        review_info = await self.vcs.get_review_info()
        changed_files = self.policy.apply_for_files(review_info.changed_files)
        if not changed_files:
            logger.info("No files to review for summary")
            return

        logger.info(f"Starting summary review: {len(changed_files)} files changed")

        rendered_files = self.diff.render_files(
            git=self.git,
            files=changed_files,
            base_sha=review_info.base_sha,
            head_sha=review_info.head_sha,
        )
        prompt_context = build_prompt_context_from_review_info(review_info)
        prompt = self.prompt.build_summary_request(rendered_files, prompt_context)

        # Inject currently created inline comments into prompt context
        current_inline_comments = self.review_comment_gateway.created_inline_comments

        if current_inline_comments:
            inline_feedback = "\n".join([
                f"- {c.file}:{c.line} ({c.severity}): {c.message}"
                for c in current_inline_comments
            ])
            prompt += f"\n\nHere is the detailed inline feedback/issues identified on specific lines:\n{inline_feedback}"

        prompt_system = self.prompt.build_system_summary_request(prompt_context)
        prompt_result = await self.review_llm_gateway.ask(prompt, prompt_system)

        summary = self.summary_comment.parse_model_output(prompt_result)
        if not summary.text.strip():
            logger.warning("Summary LLM output was empty, skipping comment")
            return

        # Calculate issue and suggestion counts
        issues_count = 0
        suggestions_count = 0

        def normalize_path(path: str) -> str:
            return path.strip().replace("\\", "/").lstrip("/")

        file_issues = {normalize_path(f): [0, 0] for f in changed_files}

        for c in current_inline_comments:
            file_path = normalize_path(c.file)
            if file_path not in file_issues:
                file_issues[file_path] = [0, 0]
            if c.severity in ("CRITICAL", "WARNING"):
                issues_count += 1
                file_issues[file_path][0] += 1
            else:
                suggestions_count += 1
                file_issues[file_path][1] += 1


        # Determine status and recommendation.
        # Formal PR reviews are only submitted for clean APPROVED results.
        # COMMENT reviews with "Address before merge" / "Suggestions only" are
        # redundant with the summary issue comment and clutter the conversation.
        if issues_count > 0:
            status_line = f"Status: {issues_count} Issue{'s' if issues_count > 1 else ''} Found | Recommendation: Address before merge"
            review_event = None
            review_body = None
        elif suggestions_count > 0:
            status_line = f"Status: Suggestions Only | Recommendation: Comment"
            review_event = None
            review_body = None
        else:
            status_line = f"Status: No Issues Found | Recommendation: Merge"
            review_event = "APPROVED"
            review_body = "Approved by AI reviewer"

        # Build file list breakdown
        file_list_lines = []
        for f, (i_c, s_c) in file_issues.items():
            parts = []
            if i_c > 0:
                parts.append(f"{i_c} issue(s)")
            if s_c > 0:
                parts.append(f"{s_c} suggestion(s)")
            if not parts:
                parts.append("0 issues")
            file_list_lines.append(f"- {f} - {', '.join(parts)}")
        file_list_section = "\n".join(file_list_lines)

        # Build current commit link
        current_sha = review_info.head_sha[:7] if review_info.head_sha else ""
        commit_link = ""
        if current_sha:
            commit_url = await self.vcs.get_commit_url(review_info.head_sha)
            if commit_url:
                commit_link = f"[{current_sha}]({commit_url})"
            else:
                commit_link = f"`{current_sha}`"


        # Format final text comment in Kilocode format
        final_text = (
            f"{status_line}\n\n"
            f"{summary.text.strip()}\n\n"
            f"{file_list_section}"
        )
        if commit_link:
            final_text += f"\n\n{commit_link}"

        summary.text = final_text

        logger.info(f"Posting summary review comment ({len(summary.text)} chars)")
        await self.review_comment_gateway.process_summary_comment(summary)

        if review_event:
            head_sha = review_info.head_sha or ""
            logger.info(f"Submitting formal review: event={review_event}, commit_id={head_sha}")
            try:
                await self.vcs.submit_review(
                    commit_id=head_sha,
                    event=review_event,
                    body=review_body,
                )
            except Exception as error:
                logger.warning(f"Failed to submit formal review: {error}")
        else:
            logger.info("Skipping formal COMMENT review; summary issue comment is authoritative")

        await hook.emit_summary_review_complete(self.cost.aggregate())


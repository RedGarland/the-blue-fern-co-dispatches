# AI Review Workflow

This repository allows optional AI assistance, but AI output is advisory until a human explicitly approves the next step.

## Supported Roles

- Codex implementation agent
- Assistant prompt/review coordinator
- GitHub Actions validation gate
- Optional AI reviewer
- Human release approver

## Operating Rules

- AI agents may assist with implementation, review, summarization, and test suggestions.
- AI agents must not be treated as release authority.
- AI agents must not publish, push, or sync Pages unless explicitly instructed.
- AI review is advisory unless the user explicitly changes the workflow.
- Human approval remains required before public release.
- GitHub Actions and publish-scope validation remain the authoritative gates.
- Dry-run success is not permission to publish.
- Pages repo sync requires explicit instruction and publish-scope validation.
- AI tools must report what they changed, what they checked, and what they intentionally did not touch.

## Recommended Usage

1. Use the Codex implementation agent to make a scoped change.
2. Use an AI reviewer, if desired, to summarize risks and identify likely regressions.
3. Validate with local tests and GitHub Actions gates.
4. Resolve review comments with new commits.
5. Treat release and publish as a separate explicit step.


import os
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


DEFAULT_MODEL = os.getenv("AI_REVIEW_MODEL", "anthropic/claude-sonnet-5")
DEFAULT_INPUT = "reports/group_chats_for_ai_review.md"
DEFAULT_OUTPUT = "reports/group_chats_ai_reviewed.md"
HASH_SEPARATOR = "#" * 30
STAR_SEPARATOR = "*" * 30
REVIEW_MARKER = "**AI Review:**"
USAGE_MARKER = "**AI Review Usage:**"


class Command(BaseCommand):
    help = "Review exported group chats with OpenRouter AI, one conversation at a time."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            "-i",
            type=str,
            default=DEFAULT_INPUT,
            help=f"Markdown export to review (default: {DEFAULT_INPUT})",
        )
        parser.add_argument(
            "--output",
            "-o",
            type=str,
            default=DEFAULT_OUTPUT,
            help=f"Reviewed Markdown output path (default: {DEFAULT_OUTPUT})",
        )
        parser.add_argument(
            "--model",
            type=str,
            default=DEFAULT_MODEL,
            help=f"OpenRouter model (default: AI_REVIEW_MODEL env or {DEFAULT_MODEL})",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of new conversations to review in this run.",
        )
        parser.add_argument(
            "--max-input-chars",
            type=int,
            default=12000,
            help="Maximum compacted conversation characters sent per AI request.",
        )
        parser.add_argument(
            "--max-output-tokens",
            type=int,
            default=900,
            help="Maximum tokens requested from the model per conversation.",
        )
        parser.add_argument(
            "--review-template",
            type=str,
            default=None,
            help="Optional file with extra review-output instructions/template.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rewrite output and re-review conversations already present.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and show what would be reviewed without calling OpenRouter or writing output.",
        )

    def handle(self, *args, **options):
        input_path = Path(options["input"])
        output_path = Path(options["output"])
        force = options["force"]
        dry_run = options["dry_run"]

        if not input_path.exists():
            raise CommandError(f"Input file does not exist: {input_path}")

        input_text = input_path.read_text(encoding="utf-8", errors="replace")
        conversations = self._parse_conversations(input_text)
        if not conversations:
            raise CommandError(f"No conversations found in {input_path}")

        reviewed_sids = set()
        if output_path.exists() and not force:
            reviewed_sids = self._reviewed_sids(output_path.read_text(encoding="utf-8", errors="replace"))

        pending = [conversation for conversation in conversations if conversation["sid"] not in reviewed_sids]
        if options["limit"] is not None:
            pending = pending[: options["limit"]]

        self.stdout.write(
            "Review setup: "
            f"input={input_path}, output={output_path}, model={options['model']}, "
            f"conversations={len(conversations)}, already_reviewed={len(reviewed_sids)}, "
            f"pending_this_run={len(pending)}, force={force}, dry_run={dry_run}"
        )

        if dry_run:
            for conversation in pending[:10]:
                compacted = self._compact_for_ai(conversation["text"], options["max_input_chars"])
                self.stdout.write(
                    f"Would review {conversation['sid']} "
                    f"(original_chars={len(conversation['text'])}, compacted_chars={len(compacted)})"
                )
            return

        client = self._openrouter_client()
        review_template = self._load_review_template(options["review_template"])

        if force or not output_path.exists():
            self._write_review_header(output_path, input_path, options, len(conversations))
        else:
            self._refresh_total_usage(output_path)

        reviewed_now = 0
        for conversation in pending:
            review, usage = self._review_conversation(
                client=client,
                model=options["model"],
                conversation_text=conversation["text"],
                max_input_chars=options["max_input_chars"],
                max_output_tokens=options["max_output_tokens"],
                review_template=review_template,
            )
            self._append_reviewed_conversation(output_path, conversation["text"], review, usage)
            self._refresh_total_usage(output_path)
            reviewed_now += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Reviewed {conversation['sid']} ({reviewed_now}/{len(pending)}) "
                    f"tokens={usage['total_tokens'] or 'N/A'} cost={self._format_cost(usage['cost_usd'])}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Finished AI review: reviewed_now={reviewed_now}, output={output_path}"
            )
        )

    def _openrouter_client(self):
        api_key = os.getenv("OPENROUTER_API_KEY") or ""
        if not api_key:
            raise CommandError("OPENROUTER_API_KEY is not set")

        try:
            from openai import OpenAI
        except Exception as exc:
            raise CommandError(f"openai package is not available: {exc}") from exc

        return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    def _load_review_template(self, template_path):
        if not template_path:
            return ""

        path = Path(template_path)
        if not path.exists():
            raise CommandError(f"Review template file does not exist: {path}")
        return path.read_text(encoding="utf-8", errors="replace").strip()

    def _parse_conversations(self, input_text):
        matches = list(re.finditer(r"(?m)^\*\*ConversationId:\*\* `(?P<sid>[^`]+)`", input_text))
        conversations = []

        for index, match in enumerate(matches):
            start = self._conversation_start(input_text, match.start())
            end = matches[index + 1].start() if index + 1 < len(matches) else len(input_text)
            group_header = self._current_group_header(input_text, match.start())
            text = input_text[start:end].strip()
            if group_header and "**Apartment:**" not in text.splitlines()[:4]:
                text = f"{group_header}\n{text}"
            conversations.append({"sid": match.group("sid"), "text": text})

        return conversations

    def _conversation_start(self, input_text, conversation_id_pos):
        hash_pos = input_text.rfind(f"\n{HASH_SEPARATOR}\n", 0, conversation_id_pos)
        star_pos = input_text.rfind(f"\n{STAR_SEPARATOR}\n", 0, conversation_id_pos)
        return max(hash_pos, star_pos, 0)

    def _current_group_header(self, input_text, conversation_id_pos):
        group_start = input_text.rfind(f"\n{HASH_SEPARATOR}\n", 0, conversation_id_pos)
        search_from = max(group_start, 0)
        header_text = input_text[search_from:conversation_id_pos]
        match = re.search(
            r"(?m)^\*\*Apartment:\*\* `(?P<apartment>[^`]+)`\n\*\*Booking:\*\* `(?P<booking>[^`]+)`",
            header_text,
        )
        if not match:
            return ""
        return (
            f"**Apartment:** `{match.group('apartment')}`\n"
            f"**Booking:** `{match.group('booking')}`"
        )

    def _reviewed_sids(self, output_text):
        return set(re.findall(r"(?m)^\*\*ConversationId:\*\* `([^`]+)`", output_text))

    def _write_review_header(self, output_path, input_path, options, total_conversations):
        directory = output_path.parent
        if str(directory):
            directory.mkdir(parents=True, exist_ok=True)

        header = [
            "# AI Reviewed Group Chat Export",
            "",
            "## Review Statistics",
            f"- **Generated at:** `{timezone.now().isoformat()}`",
            "- **Write behavior:** output is updated after each reviewed conversation",
            f"- **Source file:** `{input_path}`",
            f"- **Total source conversations:** `{total_conversations}`",
            f"- **Model:** `{options['model']}`",
            f"- **Max input chars per request:** `{options['max_input_chars']}`",
            f"- **Max output tokens per request:** `{options['max_output_tokens']}`",
            "- **Total prompt tokens so far:** `0`",
            "- **Total completion tokens so far:** `0`",
            "- **Total tokens so far:** `0`",
            "- **Total estimated cost so far:** `unknown`",
            "",
            "## Reviewed Conversations",
            "",
        ]
        output_path.write_text("\n".join(header), encoding="utf-8")

    def _append_reviewed_conversation(self, output_path, conversation_text, review, usage):
        with output_path.open("a", encoding="utf-8") as reviewed_file:
            reviewed_file.write("\n")
            reviewed_file.write(conversation_text.rstrip())
            reviewed_file.write("\n\n")
            reviewed_file.write(REVIEW_MARKER)
            reviewed_file.write("\n")
            reviewed_file.write(review.strip())
            reviewed_file.write("\n")
            reviewed_file.write(USAGE_MARKER)
            reviewed_file.write("\n")
            reviewed_file.write(f"- **Prompt tokens:** `{usage['prompt_tokens'] or 'unknown'}`\n")
            reviewed_file.write(f"- **Completion tokens:** `{usage['completion_tokens'] or 'unknown'}`\n")
            reviewed_file.write(f"- **Total tokens:** `{usage['total_tokens'] or 'unknown'}`\n")
            reviewed_file.write(f"- **Estimated cost:** `{self._format_cost(usage['cost_usd'])}`\n")

    def _review_conversation(
        self,
        client,
        model,
        conversation_text,
        max_input_chars,
        max_output_tokens,
        review_template,
    ):
        compacted = self._compact_for_ai(conversation_text, max_input_chars)
        system_msg = (
            "You are a senior property-management AI reviewer. "
            "Review one tenant-manager-AI group chat conversation at a time. "
            "Use manager replies as the strongest signal only when they clearly answer the same tenant issue. "
            "If no manager reply answers the same issue, write `none` for manager-based evidence. "
            "Respect chronological order and do not treat earlier manager messages as answers to later tenant questions. "
            "Consider Global Knowledge Base and Apartment Knowledge Base separately when they appear in context. "
            "Be concise. Do not repeat the whole transcript. Return only Markdown bullets in the requested template."
        )
        user_msg = self._review_prompt(compacted, review_template)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0,
            max_tokens=max_output_tokens,
            extra_body={"usage": {"include": True}},
        )
        review = (response.choices[0].message.content or "").strip()
        return review, self._response_usage(response)

    def _response_usage(self, response):
        usage = getattr(response, "usage", None)
        return {
            "prompt_tokens": self._usage_number(usage, "prompt_tokens"),
            "completion_tokens": self._usage_number(usage, "completion_tokens"),
            "total_tokens": self._usage_number(usage, "total_tokens"),
            "cost_usd": self._usage_cost(usage),
        }

    def _usage_number(self, usage, key):
        value = self._usage_value(usage, key)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _usage_cost(self, usage):
        for key in ("cost", "total_cost", "cost_usd", "total_cost_usd"):
            value = self._usage_value(usage, key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
        return None

    def _usage_value(self, usage, key):
        if usage is None:
            return None
        if isinstance(usage, dict):
            return usage.get(key)
        return getattr(usage, key, None)

    def _format_cost(self, cost):
        if cost is None:
            return "unknown"
        return f"${cost:.6f}"

    def _refresh_total_usage(self, output_path):
        text = output_path.read_text(encoding="utf-8", errors="replace")
        totals = self._parse_usage_totals(text)
        replacements = {
            r"- \*\*Total prompt tokens so far:\*\* `[^`]*`": f"- **Total prompt tokens so far:** `{totals['prompt_tokens']}`",
            r"- \*\*Total completion tokens so far:\*\* `[^`]*`": f"- **Total completion tokens so far:** `{totals['completion_tokens']}`",
            r"- \*\*Total tokens so far:\*\* `[^`]*`": f"- **Total tokens so far:** `{totals['total_tokens']}`",
            r"- \*\*Total estimated cost so far:\*\* `[^`]*`": f"- **Total estimated cost so far:** `{self._format_cost(totals['cost_usd'])}`",
        }

        updated = text
        for pattern, replacement in replacements.items():
            if re.search(pattern, updated):
                updated = re.sub(pattern, replacement, updated, count=1)

        if updated != text:
            output_path.write_text(updated, encoding="utf-8")

    def _parse_usage_totals(self, output_text):
        prompt_tokens = self._sum_int_lines(output_text, r"- \*\*Prompt tokens:\*\* `(\d+)`")
        completion_tokens = self._sum_int_lines(output_text, r"- \*\*Completion tokens:\*\* `(\d+)`")
        total_tokens = self._sum_int_lines(output_text, r"- \*\*Total tokens:\*\* `(\d+)`")
        costs = [float(value) for value in re.findall(r"- \*\*Estimated cost:\*\* `\$(\d+(?:\.\d+)?)`", output_text)]
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": sum(costs) if costs else None,
        }

    def _sum_int_lines(self, output_text, pattern):
        return sum(int(value) for value in re.findall(pattern, output_text))

    def _review_prompt(self, conversation_text, review_template):
        default_template = """
Return exactly this Markdown shape:
- **Needs improvement:** `yes|no`
- **Main issue:** short sentence or `none`
- **AI answer quality:** short assessment of AI answer(s), especially `Sent: no` test answers
- **Manager better answer:** quote/summarize the manager answer that clearly answers the same tenant issue, or `none`
- **Better AI answer suggestion:** concise replacement answer the AI should have sent, or `none`
- **Global KB suggestion:** what should be added/changed in global KB, or `none`
- **Apartment KB suggestion:** what should be added/changed in apartment KB, or `none`
- **System prompt suggestion:** concrete edit for `ai_answer_system`, or `none`
- **Reason:** one short explanation using only this conversation
""".strip()

        if review_template:
            default_template = f"{default_template}\n\nAdditional user template/instructions:\n{review_template}"

        return (
            f"{default_template}\n\n"
            "Conversation to review (compacted for token economy):\n"
            "```md\n"
            f"{conversation_text}\n"
            "```"
        )

    def _compact_for_ai(self, conversation_text, max_input_chars):
        compacted = self._compact_context_blocks(conversation_text)
        if len(compacted) <= max_input_chars:
            return compacted

        head_chars = max_input_chars // 2
        tail_chars = max_input_chars - head_chars
        return (
            compacted[:head_chars].rstrip()
            + "\n\n[...conversation clipped for token economy...]\n\n"
            + compacted[-tail_chars:].lstrip()
        )

    def _compact_context_blocks(self, conversation_text):
        pattern = re.compile(
            r"(?P<prefix>- \*\*Context:\*\* )(?P<context>.*?)(?=\n\*\*(?:Customer Message|Manager Message|AI Answer Message|Context)|\n[*#]{30}|\Z)",
            re.DOTALL,
        )

        def replace(match):
            context = match.group("context").strip()
            return match.group("prefix") + self._compact_context_text(context)

        return pattern.sub(replace, conversation_text)

    def _compact_context_text(self, context):
        if not context or context == "none":
            return "none"

        sections = []
        for title in (
            "GLOBAL KNOWLEDGE BASE",
            "APARTMENT KNOWLEDGE BASE",
            "CURRENT BOOKING",
            "RECENT CHAT HISTORY",
        ):
            section = self._extract_context_section(context, title)
            if section:
                sections.append(f"=== {title} ===\n{self._clip(section, 1800)}")

        if sections:
            return "\n\n".join(sections)

        return self._clip(context, 2500)

    def _extract_context_section(self, context, title):
        match = re.search(
            rf"=== {re.escape(title)} ===\n(?P<body>.*?)(?=\n\n=== [A-Z0-9 &]+ ===|\Z)",
            context,
            re.DOTALL,
        )
        if not match:
            return ""
        return match.group("body").strip()

    def _clip(self, value, max_chars):
        value = str(value or "").strip()
        if len(value) <= max_chars:
            return value
        return value[:max_chars].rstrip() + "\n[...clipped...]"

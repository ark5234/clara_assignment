"""
Agent prompt generator — converts an AgentConfig into a production-ready
Retell AI system prompt.

Design principles:
- Every section is only rendered when data exists (no placeholder gaps)
- Unknown/unconfirmed values are called out explicitly rather than silently skipped
- The prompt structure follows the exact flows specified in the Clara assignment:
    Business hours flow: greeting → purpose → collect info → transfer → fallback → wrap up
    After-hours flow:    greeting → purpose → confirm emergency → route → wrap up
"""
from __future__ import annotations

from schemas.agent_config import AgentConfig, BusinessHours, EmergencyDefinition
from utils.logger import get_logger

logger = get_logger(__name__)


class PromptGenerator:
    def generate(self, config: AgentConfig) -> str:
        """Return the full Clara system prompt for this config."""
        sections = [
            self._header(config),
            self._identity(config),
            self._business_hours_section(config),
            self._business_hours_flow(config),
            self._after_hours_flow(config),
            self._emergency_definitions(config),
            self._routing_rules(config),
            self._integration_rules(config),
            self._prohibited_behaviors(config),
        ]
        prompt = "\n\n".join(s for s in sections if s.strip())
        logger.debug(f"[{config.config_id}] Prompt generated ({len(prompt)} chars)")
        return prompt

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------
    def _header(self, c: AgentConfig) -> str:
        client_name = c.client.name or "the company"
        return (
            f"# CLARA AI VOICE AGENT — {client_name.upper()}\n"
            f"# Config ID: {c.config_id} | Version: {c.version} | Generated: {c.updated_at[:10]}\n"
            f"# Industry: {c.client.industry or 'service trade'}"
        )

    def _identity(self, c: AgentConfig) -> str:
        client_name = c.client.name or "the company"
        industry_note = ""
        if c.client.service_types:
            industry_note = (
                f" You handle calls related to: {', '.join(c.client.service_types)}."
            )
        return f"""## IDENTITY & ROLE
You are Clara, a professional AI voice receptionist for {client_name}.{industry_note}
You handle all incoming calls with warmth, clarity, and efficiency.
Your job is to get callers the right help quickly — emergencies go immediately to the on-call team; non-urgent requests are logged for follow-up.

Always be:
- **Professional and calm** — especially during emergencies
- **Clear and concise** — do not ramble or ask unnecessary questions
- **Empathetic** — acknowledge the caller's situation before acting
- **Accurate** — never invent information, ETAs, or commitments you cannot confirm"""

    def _business_hours_section(self, c: AgentConfig) -> str:
        bh = c.business_hours
        client_name = c.client.name or "the company"

        if not bh:
            return (
                "## BUSINESS HOURS\n"
                f"⚠️ Business hours for {client_name} have not yet been confirmed. "
                "Treat all calls as if office status is unknown until this is resolved."
            )

        if bh.is_confirmed:
            tz_note = f" ({bh.timezone})" if bh.timezone else ""
            schedule = bh.human_readable()
            return f"## BUSINESS HOURS\n{client_name} office hours{tz_note}:\n{schedule}"
        else:
            note = bh.notes or "Hours not yet confirmed — treat as approximate."
            return f"## BUSINESS HOURS\n⚠️ Approximate hours (not confirmed): {note}"

    def _business_hours_flow(self, c: AgentConfig) -> str:
        client_name = c.client.name or "the company"
        bh = c.business_hours
        tz = f" {bh.timezone}" if bh and bh.timezone else ""
        hours_desc = "during business hours"
        if bh and bh.is_confirmed and bh.is_fully_specified():
            hours_desc = f"Monday–Friday {bh.monday.open}–{bh.friday.close}{tz}"

        # Build transfer step
        transfer_steps = self._format_transfer_steps(c, after_hours=False)

        return f"""## CALL FLOW A — BUSINESS HOURS ({hours_desc})

### Step 1 — Greeting
Say: *"Thank you for calling {client_name}. This is Clara. How can I help you today?"*

### Step 2 — Identify Purpose
Listen carefully to determine whether this is:
- An **EMERGENCY** (see Emergency Definitions below)
- A **non-emergency** service request or inquiry

### Step 3 — Collect Caller Information
Always collect before transferring:
- Full name: *"May I get your full name?"*
- Callback number: *"And what is the best phone number to reach you?"*

For emergencies, also collect:
- Service address: *"What is the service address?"*

### Step 4 — Transfer / Route
{transfer_steps}

### Step 5 — Fallback if Transfer Fails
If the transfer is not answered or fails, say:
*"I wasn't able to connect you with our team right now. Your information has been logged and someone will call you back shortly."*

### Step 6 — Wrap Up
Ask: *"Is there anything else I can help you with today?"*
If no: *"Thank you for calling {client_name}. Have a great day!"*"""

    def _after_hours_flow(self, c: AgentConfig) -> str:
        client_name = c.client.name or "the company"
        bh = c.business_hours
        hours_desc = "during business hours"
        if bh and bh.is_confirmed and bh.is_fully_specified():
            hours_desc = f"Monday–Friday {bh.monday.open}–{bh.friday.close}"
            if bh.timezone:
                hours_desc += f" {bh.timezone}"

        # Build emergency transfer in after-hours context
        emergency_transfer = self._format_transfer_steps(c, after_hours=True)

        return f"""## CALL FLOW B — AFTER HOURS (outside business hours)

### Step 1 — Greeting
Say: *"Thank you for calling {client_name}. Our office is currently closed. This is Clara — how can I help?"*

### Step 2 — Identify Purpose
Listen carefully to determine the nature of the call.

### Step 3 — Confirm Emergency
Ask: *"Is this an emergency situation that requires immediate attention?"*

### Step 4A — If EMERGENCY
Acknowledge: *"I understand — I'll connect you with our on-call team right away."*

Collect **immediately before transfer**:
1. Full name: *"May I get your full name?"*
2. Callback number: *"What number can we reach you at?"*
3. Service address: *"What is the service address?"*

{emergency_transfer}

**If transfer fails:**
Say: *"I wasn't able to reach our on-call team at this moment. Your details have been recorded and someone will contact you as soon as possible. If this is a life-threatening emergency, please call 911."*

### Step 4B — If NON-EMERGENCY
Say: *"I understand. Since our office is currently closed, I'll take your information and have our team follow up with you during business hours."*

Collect:
1. Full name
2. Callback phone number
3. Description of the issue or request

Confirm: *"Our team will follow up with you {hours_desc}."*

### Step 5 — Wrap Up
Ask: *"Is there anything else I can help you with?"*
If no: *"Thank you for calling {client_name}. We'll be in touch soon."*"""

    def _format_transfer_steps(self, c: AgentConfig, after_hours: bool) -> str:
        if not c.emergency_definitions:
            return (
                "⚠️ Transfer targets have not yet been configured. "
                "Contact the onboarding team to complete routing setup."
            )

        lines = []
        for ed in c.emergency_definitions:
            target = ed.transfer_target
            timeout = ed.transfer_timeout_seconds
            fallback = ed.fallback_on_timeout or "Log information and assure callback"

            target_desc = "on-call team"
            if target:
                target_desc = target.name
                if target.phone:
                    target_desc += f" ({target.phone})"

            timeout_note = f" (timeout: {timeout}s)" if timeout else ""
            lines.append(
                f"**If '{ed.type}' emergency:** Transfer to {target_desc}{timeout_note}\n"
                f"  — Fallback: {fallback}"
            )

        if not lines:
            return "Transfer target not yet confirmed — flag for onboarding follow-up."

        return "\n".join(lines)

    def _emergency_definitions(self, c: AgentConfig) -> str:
        if not c.emergency_definitions:
            return (
                "## EMERGENCY DEFINITIONS\n"
                "⚠️ No emergency types have been confirmed yet. "
                "Treat any caller expressing urgency as a potential emergency until this is resolved."
            )

        rows = ["| Emergency Type | Description | Trigger Keywords |", "|---|---|---|"]
        for ed in c.emergency_definitions:
            keywords = ", ".join(ed.keywords) if ed.keywords else "—"
            rows.append(f"| {ed.type} | {ed.description} | {keywords} |")

        return "## EMERGENCY DEFINITIONS\nThe following situations require IMMEDIATE transfer:\n\n" + "\n".join(rows)

    def _routing_rules(self, c: AgentConfig) -> str:
        lines = ["## ROUTING RULES", "", "| Condition | Transfer Target | Timeout | Fallback |", "|---|---|---|---|"]

        has_rules = False
        for ed in c.emergency_definitions:
            if ed.transfer_target:
                target = ed.transfer_target
                target_str = target.name
                if target.phone:
                    target_str += f" ({target.phone})"
                timeout = f"{ed.transfer_timeout_seconds}s" if ed.transfer_timeout_seconds else "Default"
                fallback = ed.fallback_on_timeout or "Log and assure callback"
                lines.append(f"| {ed.type} emergency | {target_str} | {timeout} | {fallback} |")
                has_rules = True

        ner = c.non_emergency_routing
        if ner:
            if ner.business_hours_target:
                t = ner.business_hours_target
                t_str = t.name + (f" ({t.phone})" if t.phone else "")
                lines.append(f"| Non-emergency (business hours) | {t_str} | — | Log and callback |")
                has_rules = True
            if ner.after_hours_action:
                action_desc = {
                    "collect_and_callback": "Collect info, callback during business hours",
                    "voicemail": "Direct to voicemail",
                    "transfer": "Transfer to after-hours line",
                }.get(ner.after_hours_action, ner.after_hours_action)
                lines.append(f"| Non-emergency (after hours) | {action_desc} | — | — |")
                has_rules = True

        if not has_rules:
            return "## ROUTING RULES\n⚠️ Routing rules not yet confirmed. Flag for onboarding follow-up."

        return "\n".join(lines)

    def _integration_rules(self, c: AgentConfig) -> str:
        if not c.integration or not c.integration.constraints:
            if c.integration and c.integration.system:
                return (
                    f"## INTEGRATION RULES ({c.integration.system})\n"
                    f"⚠️ {c.integration.system} is in use but specific constraints have not yet been confirmed."
                )
            return ""

        system = c.integration.system or "Integration system"
        lines = [f"## INTEGRATION RULES ({system})"]
        for constraint in c.integration.constraints:
            lines.append(f"\n**Rule:** {constraint.rule_description}")
            if constraint.job_types_excluded:
                lines.append(
                    f"- NEVER auto-create jobs for: {', '.join(constraint.job_types_excluded)}"
                )
            if constraint.job_types_auto_create:
                lines.append(
                    f"- MAY auto-create jobs for: {', '.join(constraint.job_types_auto_create)}"
                )
        return "\n".join(lines)

    def _prohibited_behaviors(self, c: AgentConfig) -> str:
        rules = [
            "Never fabricate information about technician availability, ETAs, or job status.",
            "Never share internal phone numbers with callers.",
            "Never promise a specific callback time unless it is confirmed in the routing rules.",
            "Never create the impression the office is open when it is after hours.",
            "Never dismiss a caller expressing safety concerns — always treat with urgency.",
        ]

        # Add integration-specific prohibited behaviors
        if c.integration and c.integration.constraints:
            for constraint in c.integration.constraints:
                for jt in constraint.job_types_excluded:
                    rules.append(
                        f"Never auto-create {constraint.system} jobs for '{jt}' call types."
                    )

        # Add from special_rules
        rules.extend(c.special_rules)

        bullet_list = "\n".join(f"- {r}" for r in rules)
        return f"## PROHIBITED BEHAVIORS\n{bullet_list}"

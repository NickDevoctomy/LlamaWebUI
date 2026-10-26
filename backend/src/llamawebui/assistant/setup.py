"""Build the assistant from the app's services."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from llamawebui.assistant.brief import Brief, brief_text
from llamawebui.assistant.conversations import ConversationRegistry
from llamawebui.assistant.engine import AssistantEngine, TurnSetup
from llamawebui.assistant.openrouter_client import ChatClient, OpenRouterError
from llamawebui.assistant.prompt import render_prompt, system_template
from llamawebui.assistant.settings import AssistantSettings, load_settings
from llamawebui.assistant.tools import Tool, ToolServices
from llamawebui.assistant.tools.arguments import ARGUMENT_TOOLS
from llamawebui.assistant.tools.system import SYSTEM_TOOLS
from llamawebui.domain.benchmark import Benchmark
from llamawebui.models import AssistantConversationRecord
from llamawebui.services.hardware_profile import HardwareProfile
from llamawebui.services.profile_registry import ProfileNotFoundError, ProfileRegistry
from llamawebui.services.settings_store import OPENROUTER_MODEL, SettingsStore

TOOLS: tuple[Tool, ...] = (*SYSTEM_TOOLS, *ARGUMENT_TOOLS)
GIB = 1024**3


def hardware_text(hardware: HardwareProfile) -> str:
    lines = [
        f"- CPU: {hardware.cpu or 'unknown'}",
        f"- System memory: {hardware.memory_bytes / GIB:.1f} GiB",
        f"- GPUs: {', '.join(hardware.gpus) or 'none detected'}",
    ]
    if hardware.discrete_vram_bytes:
        lines.append(f"- Discrete VRAM (NVIDIA): {hardware.discrete_vram_bytes / GIB:.1f} GiB")
    else:
        lines.append(
            "- Discrete VRAM: not reported (only NVIDIA cards report it; integrated GPUs "
            "share system memory)"
        )
    return "\n".join(lines)


def build_engine(
    *,
    conversations: ConversationRegistry,
    client: ChatClient,
    services: ToolServices,
    settings_store: SettingsStore,
    profiles: ProfileRegistry,
    benchmarks: Callable[[], Sequence[Benchmark]],
    tools: Sequence[Tool] = TOOLS,
) -> AssistantEngine:
    def settings() -> AssistantSettings:
        return load_settings(settings_store)

    def setup(conversation: AssistantConversationRecord) -> TurnSetup:
        model = settings_store.get_json(OPENROUTER_MODEL)
        if model is None or not isinstance(model.get("id"), str):
            raise OpenRouterError("no OpenRouter model is selected; choose one in Settings")
        brief = Brief.model_validate(conversation.brief)
        alias = None
        if brief.profile_id is not None:
            try:
                alias = profiles.get(brief.profile_id).alias
            except ProfileNotFoundError:
                alias = None
        benchmark = next(
            (item for item in benchmarks() if item.id == brief.benchmark_id), None
        )
        prompt = render_prompt(
            system_template(),
            {
                "assistant_name": settings().name,
                "date": date.today().isoformat(),
                "hardware": hardware_text(services.hardware()),
                "brief": brief_text(
                    brief,
                    profile_alias=alias,
                    benchmark_title=benchmark.title if benchmark else str(brief.benchmark_id),
                ),
            },
        )
        pricing = model.get("pricing")
        return TurnSetup(
            model_id=model["id"],
            pricing=pricing if isinstance(pricing, dict) else {},
            system_prompt=prompt,
        )

    return AssistantEngine(
        conversations, client, services=services, tools=tools, settings=settings, setup=setup
    )

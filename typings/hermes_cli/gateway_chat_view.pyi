import asyncio
from typing import Protocol

from hermes_cli.gateway_client import GatewayClient

class Emitter(Protocol):
    def on_text_delta(self, text: str | None) -> None: ...
    # GatewayChatView passes the first four by position: the event type, the tool's name, its preview text and its
    # arguments; the call id, result and error flag come as keywords.
    def on_tool_progress(self, event_type: str, tool_name: str | None = None, preview: str | None = None,
                         args: object = None, /, **values: object) -> None: ...

class GatewayChatView:
    session_id: str
    prompts: dict[str, dict[str, object]]
    completions: dict[str, dict[str, object]]
    changed: asyncio.Event
    failure: BaseException | None
    def __init__(self, client: GatewayClient, snapshot: dict[str, object], *, quiet: bool = False,
                 emitter: Emitter | None = None, usage_file: str | None = None) -> None: ...
    def show_prompt(self, prompt: dict[str, object]) -> None: ...
    async def render(self) -> None: ...
    async def submit(self, text: str) -> dict[str, object]: ...
    async def command(self, text: str) -> bool: ...

from dataclasses import dataclass
from pathlib import Path

@dataclass
class ProfileInfo:
    name: str
    path: Path
    is_default: bool
    gateway_running: bool
    model: str | None = None
    provider: str | None = None

def list_profiles(*, lazy_skill_count: bool = False) -> list[ProfileInfo]: ...

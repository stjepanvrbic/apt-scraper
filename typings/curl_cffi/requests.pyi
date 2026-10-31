from collections.abc import Mapping

class RequestsError(Exception): ...

class Response:
    status_code: int
    text: str

class Session:
    def __init__(self, *, impersonate: str | None = None) -> None: ...
    def get(self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float | None = None) -> Response: ...

"""A Sightengine stand-in behind httpx.MockTransport: no test ever reaches the network."""

import re

import httpx

from app.platform.sightengine import Credentials, RequestSpacer, Sightengine

TEST_USER = "test-user-4821"
TEST_SECRET = "test-secret-9f3a"

Answer = float | httpx.Response | Exception


def success(score: float) -> httpx.Response:
    body = {"status": "success", "request": {"id": "req_1", "operations": 5}, "type": {"ai_generated": score}}
    return httpx.Response(200, json=body)


def failure(error_type: str, code: int, status: int = 200) -> httpx.Response:
    body = {"status": "failure", "error": {"type": error_type, "code": code, "message": "refused"}}
    return httpx.Response(status, json=body)


def form_fields(request: httpx.Request) -> dict[str, bytes]:
    """The parts of a multipart request, by name."""
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    fields = {}
    for part in request.content.split(b"--" + boundary)[1:-1]:
        head, _, body = part.partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        fields[name] = body.removesuffix(b"\r\n")
    return fields


class FakeSightengine:
    """Answers each request with the next answer (the last one repeats) and keeps every request."""

    def __init__(self, *answers: Answer):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, httpx.Response) else success(answer)

    @property
    def media(self) -> list[bytes]:
        return [form_fields(r)["media"] for r in self.requests]

    def client(self, spacer: RequestSpacer | None = None, max_wait: float = 30) -> Sightengine:
        return Sightengine(
            Credentials(TEST_USER, TEST_SECRET), spacer or RequestSpacer(0), max_wait, httpx.MockTransport(self)
        )

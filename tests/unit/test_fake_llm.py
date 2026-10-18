from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from saife.errors import FixtureMissingError, LLMOutputError
from saife.llm.base import SearchHit
from saife.llm.fake import FakeLLM, FakeRequest, FakeResponse, RecordingLLM


class Out(BaseModel):
    answer: str


MSGS = [{"role": "user", "content": "q"}]


async def test_record_then_replay_roundtrip(tmp_path: Path) -> None:
    live = FakeLLM("anthropic", handler=lambda r: FakeResponse(text='{"answer": "42"}'))
    recorder = RecordingLLM(live, tmp_path)
    first = await recorder.parse(model="m", system="s", messages=MSGS, schema=Out)

    replay = FakeLLM("anthropic", fixtures_dir=tmp_path)
    second = await replay.parse(model="m", system="s", messages=MSGS, schema=Out)
    assert second.parsed == first.parsed
    assert len(list(tmp_path.glob("*.json"))) == 1


async def test_missing_fixture_raises_with_hint(tmp_path: Path) -> None:
    replay = FakeLLM("anthropic", fixtures_dir=tmp_path)
    with pytest.raises(FixtureMissingError, match="SAIFE_RECORD=1"):
        await replay.complete(model="m", system="s", messages=MSGS)


async def test_different_requests_have_different_keys() -> None:
    a = FakeRequest(provider="p", model="m", kind="complete", system="s", messages=MSGS)
    b = a.model_copy(update={"temperature": 1.0})
    assert a.key() != b.key()


async def test_invalid_fixture_output_is_an_output_error() -> None:
    fake = FakeLLM("verifier", handler=lambda r: FakeResponse(text="nope"))
    with pytest.raises(LLMOutputError):
        await fake.parse(model="m", system="s", messages=MSGS, schema=Out)


async def test_search_record_then_replay(tmp_path: Path) -> None:
    hit = SearchHit(url="https://e.org", title="E", text="cited passage")
    live = FakeLLM("anthropic", handler=lambda r: FakeResponse(text="summary", hits=[hit]))
    first = await RecordingLLM(live, tmp_path).search(
        model="m", system="s", messages=MSGS, max_uses=3
    )
    replay = FakeLLM("anthropic", fixtures_dir=tmp_path)
    second = await replay.search(model="m", system="s", messages=MSGS, max_uses=3)
    assert second.hits == first.hits == [hit]
    assert replay.calls[0].kind == "search"


def test_max_uses_is_part_of_search_keys_only() -> None:
    # Unset max_uses must not change keys of existing complete/parse fixtures.
    a = FakeRequest(provider="p", model="m", kind="complete", system="s", messages=MSGS)
    legacy = {k: v for k, v in a.model_dump().items() if k != "max_uses"}
    canonical = json.dumps(legacy, sort_keys=True, ensure_ascii=False).encode("utf-8")
    assert a.key() == hashlib.sha256(canonical).hexdigest()[:24]
    s1 = FakeRequest(provider="p", model="m", kind="search", system="s", messages=MSGS, max_uses=1)
    assert s1.key() != s1.model_copy(update={"max_uses": 2}).key()

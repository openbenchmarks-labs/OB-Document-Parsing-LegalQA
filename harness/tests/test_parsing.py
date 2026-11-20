from dataclasses import dataclass, replace
from pathlib import Path

from harness import parsing
from harness.tool_registry import TOOLS, Tool


@dataclass
class Doc:
    document_id: str
    pdf: Path
    views: dict


def fixture_tool(parse, **changes) -> Tool:
    tool = Tool(name="fixture", vendor="Example", variant="", parse=parse,
                settings={"mode": "a"}, pricing={}, tracked_changes_opt_in=False,
                bypasses_vendor_cache=False)
    return replace(tool, **changes)


def test_parse_cache_invalidates_on_pdf_and_settings(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"one")
    doc = Doc("doc", pdf, {})
    calls = []

    def fake(_pdf, _settings):
        calls.append(_pdf.read_bytes())
        return "markdown", {"n_pages": 1}

    monkeypatch.setitem(TOOLS, "fixture", fixture_tool(fake))
    parsing.render(doc, "fixture", tmp_path / "cache")
    parsing.render(doc, "fixture", tmp_path / "cache")
    assert len(calls) == 1

    pdf.write_bytes(b"two")
    parsing.render(doc, "fixture", tmp_path / "cache")
    assert len(calls) == 2

    TOOLS["fixture"] = fixture_tool(fake, settings={"mode": "b"})
    parsing.render(doc, "fixture", tmp_path / "cache")
    assert len(calls) == 3


def test_repeat_parses_use_own_slot_and_bypass_vendor_cache(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"one")
    doc = Doc("doc", pdf, {})
    calls = []

    def fake(_pdf, _settings, *, fresh=False):
        calls.append(fresh)
        return f"markdown {len(calls)}", {"n_pages": 1}

    monkeypatch.setitem(TOOLS, "fixture", fixture_tool(fake, bypasses_vendor_cache=True))
    first, meta1 = parsing.render(doc, "fixture", tmp_path / "cache", repeat=1)
    second, meta2 = parsing.render(doc, "fixture", tmp_path / "cache", repeat=2)
    again, meta3 = parsing.render(doc, "fixture", tmp_path / "cache", repeat=2)
    assert calls == [False, True]
    assert (first, second, again) == ("markdown 1", "markdown 2", "markdown 2")
    assert meta2["vendor_cache_bypassed"] is True and meta3["cached"] is True
    assert (tmp_path / "cache" / "doc.fixture.r2.md").is_file()

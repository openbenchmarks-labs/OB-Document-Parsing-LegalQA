import json
import re
from types import SimpleNamespace

from harness import dataset, runner


class FakeModels:
    """Answers reader calls with 'No.' and judges every answer correct, echoing ids."""

    def __init__(self, judge_reply=None):
        self.reader_calls = 0
        self.judge_calls = 0
        self.judge_reply = judge_reply

    def create(self, **kwargs):
        user = kwargs["input"][-1]["content"]
        if isinstance(user, str) and "Answers to bucket" in user:
            self.judge_calls += 1
            ids = re.findall(r'"id": "([0-9a-f]+)"', user)
            text = (self.judge_reply if self.judge_reply is not None else json.dumps(
                {"verdicts": [{"id": i, "bucket": "correct", "why": "agrees"} for i in ids]}))
            return SimpleNamespace(output_text=text, usage=None, model="judge")
        self.reader_calls += 1
        return SimpleNamespace(output_text="No.", usage=None, model="reader")


def fixture(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"fixture")
    view = tmp_path / "doc.oracle.md"
    view.write_text("The operative answer is No.")
    documents = {"doc": dataset.Document("doc", pdf, {"oracle": view})}
    cases = [dataset.Case("q1", "Is it operative?", "fixture", ["doc"],
                          "No.", "", {"correct": ["No"], "stale": ["Yes"], "fused": []})]
    return cases, documents


def test_mocked_workflow_writes_reusable_artifacts(tmp_path):
    cases, documents = fixture(tmp_path)
    models = FakeModels()
    client = SimpleNamespace(responses=models)
    first = runner.run_arm("oracle", cases, documents, client, "reader", tmp_path / "run",
                           "tagged", judge_model="judge")
    second = runner.run_arm("oracle", cases, documents, client, "reader", tmp_path / "run",
                            "tagged", judge_model="judge")
    assert first["score"]["accuracy"] == 1
    assert first["rows"][0]["token_bucket"] == "correct"
    assert second["rows"][0]["reused"] is True
    assert second["rows"][0]["verdict_reused"] is True
    assert models.reader_calls == 1
    assert models.judge_calls == 1


def test_repeats_and_asks_give_replicates_and_judge_identical_answers_once(tmp_path):
    cases, documents = fixture(tmp_path)
    models = FakeModels()
    client = SimpleNamespace(responses=models)
    result = runner.run_arm("oracle", cases, documents, client, "reader", tmp_path / "run",
                            "tagged", judge_model="judge", repeats=3, asks=3)
    assert len(result["rows"]) == 9
    assert {(row["repeat"], row["ask"]) for row in result["rows"]} == {
        (r, a) for r in (1, 2, 3) for a in (1, 2, 3)}
    assert models.reader_calls == 9
    # Nine identical answers to one question are one verdict, in one call.
    assert models.judge_calls == 1
    assert result["score"]["accuracy_sd"] == 0.0
    assert set(result["cost"]) == {"vendor_usd", "reader_usd", "judge_usd", "llm_usd",
                                   "total_usd"}


def test_unusable_judge_reply_leaves_answer_unscored(tmp_path):
    cases, documents = fixture(tmp_path)
    client = SimpleNamespace(responses=FakeModels(judge_reply="not json"))
    result = runner.run_arm("oracle", cases, documents, client, "reader", tmp_path / "run",
                            "tagged", judge_model="judge")
    assert result["rows"][0]["bucket"] == "unscored"
    assert result["score"]["unscored"] == 1
    assert result["score"]["n"] == 0


def test_fatal_reader_error_stops_the_run(tmp_path):
    import pytest

    from harness.vendor_errors import FatalVendorError

    cases, documents = fixture(tmp_path)

    class DeadKey:
        def create(self, **_kwargs):
            raise RuntimeError("401 invalid api key")

    with pytest.raises(FatalVendorError):
        runner.run_arm("oracle", cases, documents, SimpleNamespace(responses=DeadKey()),
                       "reader", tmp_path / "run", "tagged", judge_model="judge")


def test_empty_answer_is_not_cached(tmp_path):
    cases, documents = fixture(tmp_path)

    class Empty(FakeModels):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            if response.model == "reader":
                response.output_text = ""
            return response

    runner.run_arm("oracle", cases, documents, SimpleNamespace(responses=Empty()), "reader",
                   tmp_path / "run", "tagged", judge_model="judge")
    assert not list((tmp_path / "run" / "answers" / "tagged" / "oracle").glob("*.json"))

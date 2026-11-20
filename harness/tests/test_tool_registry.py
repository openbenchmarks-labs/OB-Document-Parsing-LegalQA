from harness.tool_registry import TOOLS


def test_dated_builds_are_in_the_fingerprint():
    assert TOOLS["extend"].settings["build"] == "2026-10-17"
    assert TOOLS["reducto"].settings["build"] == "2026-10-06"
    assert "unstructured" in TOOLS
    assert TOOLS["unstructured"].tracked_changes_opt_in is False


def test_model_setups_share_one_prompt():
    # A different prompt is a different measurement and needs its own setup name.
    assert TOOLS["astra"].settings["prompt"] == TOOLS["fable"].settings["prompt"]

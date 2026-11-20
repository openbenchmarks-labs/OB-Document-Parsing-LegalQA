from judge.grader import classify, parse_verdicts


def test_whole_token_matching_avoids_numeric_substrings():
    match = {"correct": ["5"], "stale": ["2.5%"], "fused": []}
    assert classify("The rate is 2.5%.", match) == "stale"


def test_multiple_reference_hits_are_ambiguous():
    match = {"correct": ["No"], "stale": ["Yes"], "fused": []}
    assert classify("No, not yes.", match) == "ambiguous"


def test_parse_verdicts_ignores_unknown_ids_and_disagreeing_duplicates():
    text = ('{"verdicts": [{"id": "a", "bucket": "correct", "why": "x"},'
            ' {"id": "b", "bucket": "stale"}, {"id": "b", "bucket": "correct"},'
            ' {"id": "z", "bucket": "correct"}, {"id": "c", "bucket": "nonsense"}]}')
    verdicts, missing = parse_verdicts(text, ["a", "b", "c"])
    assert verdicts == {"a": ("correct", "x")}
    assert missing == ["b", "c"]


def test_unreadable_judge_reply_is_no_verdicts():
    assert parse_verdicts("no json here", ["a"]) == ({}, ["a"])

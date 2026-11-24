import pytest

from presentation_service.fillers import LineHesitations, find_fillers
from presentation_service.transcript import count_fillers, mark_fillers


def found(text, **kwargs):
    return [label for _, _, label in find_fillers(text, **kwargs)]


@pytest.mark.parametrize("text", [
    "I'd like to show you the results.",
    "It looks like the numbers went up.",
    "Things like that happen.",
    "There is so much to do.",
    "I think so.",
    "Cats, dogs and so on.",
    "How do you know that?",
    "Let me know if you know what I mean.",
])
def test_ordinary_uses_are_not_fillers(text):
    assert found(text) == []


@pytest.mark.parametrize("text, expected", [
    ("Um, the results are uh good.", ["um", "uh"]),
    ("Uhm, er, hmmm.", ["um", "uh", "hmm"]),
    ("It was, like, really good.", ["like"]),
    ("It cost like ten dollars.", ["like"]),
    ("So the next slide shows the plan.", ["so"]),
    ("It was, you know, a good day.", ["you know"]),
])
def test_fillers_are_found(text, expected):
    assert found(text) == expected


def test_hesitations_between_words_do_not_hide_an_ordinary_use():
    assert found("I'd like um to show you.") == ["um"]


def test_curly_apostrophes_match_the_same_rules():
    assert found("I’d like to go.") == found("I'd like to go.") == []


def test_last_word_of_an_unfinished_line_waits_for_the_next_one():
    assert found("and I was like", unfinished=True) == []
    assert found("it was, you know", unfinished=True) == []
    assert found("and then um", unfinished=True) == ["um"]             # always a filler
    assert found("so the plan", unfinished=True) == ["so"]


def test_counted_fillers_are_bracketed_in_the_sentence():
    assert mark_fillers("So I think um it looks like, you know, rain.") == \
        "[So] I think [um] it looks like, [you know], rain."
    assert mark_fillers("I'd like to go.") == "I'd like to go."
    assert mark_fillers("") == ""


def test_counts_cover_every_filler():
    counts = count_fillers("Um so I, like, know. Um.")
    assert counts["um"] == 2 and counts["like"] == 1 and counts["so"] == 1 and counts["you know"] == 0


def restored(partials, final):
    line = LineHesitations()
    for p in partials:
        line.add_partial(p)
    return line.restore(final)


def test_a_hesitation_the_finished_line_lost_is_put_back():
    assert restored(["we have um", "we have um two", "We have um, two plans"], "We have two plans.") == \
        "We have um two plans."
    assert restored(["um we", "um we have"], "We have two plans.") == "um We have two plans."


def test_hesitations_are_not_put_back_when_unsure():
    assert restored(["we have um two"], "We have two plans.") == "We have two plans."            # seen once
    assert restored(["we had um", "we had um two"], "We have two plans.") == "We have two plans."  # reworded
    assert restored(["we um have", "we um have"], "We have uh two plans.") == "We have uh two plans."  # moved
    assert restored(["we um have", "we um have"], "We um have two.") == "We um have two."        # still there

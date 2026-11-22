import pytest

from script_match import Embedder, ScriptTracker, chunk_script, speech_window

SCRIPT = """
Good morning everyone, and thank you for coming to our quarterly review.
Today I will walk you through our sales results, our hiring plans, and the new product launch.
First, sales grew by twelve percent compared to last quarter, mostly in the European market.
Our biggest customer, Northwind, renewed their contract for another three years.
Second, we plan to hire fifteen new engineers to support the mobile app team.
Recruiting will focus on senior developers with experience in cloud infrastructure.
Third, the new product called Atlas launches in March with a public beta for early customers.
Atlas helps small businesses track inventory automatically using their phone camera.
To wrap up, the quarter was strong and the team is ready for the next phase.
Thank you, and I am happy to take any questions now.
"""

PARAPHRASE = [
    "Hi all, thanks so much for joining this quarterly review meeting.",
    "I'm going to cover how sales went, who we're hiring, and the product we're launching.",
    "So first up, sales went up twelve percent from last quarter, mainly thanks to Europe.",
    "And Northwind, our largest client, signed on again for three more years.",
    "Next, we want to bring on fifteen engineers for the mobile app group.",
    "We're mostly looking for senior people who know cloud infrastructure well.",
    "Third thing, Atlas, our new product, goes live in March with an early public beta.",
    "It lets small companies keep track of their stock just by pointing a phone camera.",
    "All in all it was a great quarter and the team is set for what comes next.",
    "Thanks everyone, I'll open it up for questions.",
]

OFF_TOPIC = ("My favorite pizza place downtown makes an amazing mushroom pizza with garlic. "
             "Last weekend my dog chased a squirrel up a tree in the park for an hour. "
             "I have been learning to play the guitar and my fingers really hurt from practicing chords.")


@pytest.fixture(scope="module")
def embed():
    return Embedder()


@pytest.fixture
def tracker(embed):
    return ScriptTracker(chunk_script(SCRIPT), embed)


UPDATE_S = 1.5      # simulated seconds between checks, like the app


def speak(tracker, sentences, every=8, start=0.0):
    """Feeds speech a few words at a time and at each sentence end (like the GUI's timer while
    someone talks), with a simulated clock; returns (positions, event messages)."""
    said, positions, events = [], [], []
    clock = [start]

    def update():
        clock[0] += UPDATE_S
        m = tracker.update(" ".join(said), now=clock[0])
        if m:
            positions.append(m.position)
            events.extend(e["message"] for e in m.events)

    for sentence in sentences:
        for i, w in enumerate(sentence.split(), 1):
            said.append(w)
            if len(said) % every == 0:
                update()
        update()
    return positions, events


def test_chunking():
    chunks = chunk_script(SCRIPT)
    assert len(chunks) == 10 and chunks[3].startswith("Our biggest customer")
    assert chunk_script("Hi. I am Sam. Today we talk about results and plans.") == \
        ["Hi. I am Sam. Today we talk about results and plans."]           # short ones merged


def test_window_drops_fillers():
    assert speech_window("so um I think uh the results are like good") == "i think the results are good"


def test_straight_read_reaches_the_end_in_order(tracker):
    chunks = chunk_script(SCRIPT)
    positions, events = speak(tracker, chunks)
    assert positions[-1] == len(chunks) - 1
    assert all(b >= a for a, b in zip(positions, positions[1:]))         # never moves backwards
    assert not events and not tracker.skipped


def test_paraphrased_read_still_tracks(tracker):
    positions, events = speak(tracker, PARAPHRASE)
    assert positions[-1] >= len(PARAPHRASE) - 2
    assert not [e for e in events if "back" in e]


def test_skipping_a_section_is_reported(tracker):
    chunks = chunk_script(SCRIPT)
    _, events = speak(tracker, chunks[:3] + chunks[6:9])
    jumps = [e for e in events if e.startswith("Jumped ahead")]
    assert jumps, events
    assert {3, 4, 5} <= tracker.skipped                                   # chunks 4-6 (0-based 3-5)


def test_going_back_warns_once_and_keeps_the_position(tracker):
    chunks = chunk_script(SCRIPT)
    positions, events = speak(tracker, chunks[:6] + [chunks[1], chunks[1]])
    back = [e for e in events if e.startswith("Went back to chunk 2")]
    assert len(back) == 1, events                       # one warning, not one per check
    assert tracker.position == 5                        # still at part 6: position never moves back
    assert all(b >= a for a, b in zip(positions, positions[1:]))


def test_after_going_back_reading_on_continues_from_the_kept_position(tracker):
    chunks = chunk_script(SCRIPT)
    positions, _ = speak(tracker, chunks[:6] + [chunks[1]] + chunks[6:9])
    assert tracker.position >= 7 and not tracker.skipped


def test_off_topic_is_reported_after_a_while(tracker):
    chunks = chunk_script(SCRIPT)
    _, events = speak(tracker, chunks[:2] + [OFF_TOPIC, OFF_TOPIC])     # ~100 words, about 40 s
    assert any(e.startswith("Off script") for e in events), events


def test_threshold_slider_changes_what_counts_as_off_script(embed):
    chunks = chunk_script(SCRIPT)
    # a paraphrase of the first line: off script with a strict threshold, on track with a loose one
    strict = ScriptTracker(chunks, embed, threshold=0.8)
    assert strict.update(PARAPHRASE[0], now=0).status.startswith(("off topic", "ad-libbing"))
    loose = ScriptTracker(chunks, embed, threshold=0.2)
    assert loose.update(PARAPHRASE[0], now=0).status == "on track"


def test_stuck_on_one_chunk_while_talking(embed):
    chunks = chunk_script(SCRIPT)
    t = ScriptTracker(chunks, embed)
    limit = t.stuck_after(0)                         # 20 s minimum for a short chunk
    events, now = [], 0.0
    while now < limit + 10:                          # keep repeating the first line
        m = t.update(chunks[0] + " " + chunks[0], now=now)
        events += m.events
        now += UPDATE_S
    stuck = [e for e in events if e["kind"] == "stuck"]
    assert len(stuck) == 1 and stuck[0]["cue"] == "Move on to the next part"


def test_moving_on_in_time_is_not_stuck(tracker):
    _, events = speak(tracker, chunk_script(SCRIPT))
    assert not [e for e in events if e.startswith("Stuck")]


def test_stuck_factor_setting(embed):
    chunks = chunk_script(SCRIPT)
    assert ScriptTracker(chunks, embed, stuck_factor=8).stuck_after(1) > \
        ScriptTracker(chunks, embed, stuck_factor=3).stuck_after(1)


def test_events_have_short_cues(tracker):
    chunks = chunk_script(SCRIPT)
    clock, said, cues = 0.0, [], []
    for w in " ".join(chunks[:3] + chunks[6:9]).split():
        said.append(w)
        if len(said) % 6 == 0:
            clock += UPDATE_S
            m = tracker.update(" ".join(said), now=clock)
            cues += [e["cue"] for e in m.events] if m else []
    assert "Skipped ahead" in cues, cues
    assert not any(any(ch.isdigit() for ch in c) for c in cues)     # pop-ups never name a part


def test_catch_up_counts_earlier_parts_as_visited(embed):
    chunks = chunk_script(SCRIPT)
    t = ScriptTracker(chunks, embed)
    t.catch_up(" ".join(chunks[:3]), now=10)          # started tracking after 3 parts were said
    m = t.update(" ".join(chunks[:3]), now=11.5)
    assert m.position >= 1 and not t.skipped          # parts 1-2 were visited, not skipped


def test_skipping_a_single_part_counts(tracker):
    # read part 1, skip part 2, read parts 3-6: only part 2 is skipped, and "Skipped ahead" fires
    chunks = chunk_script(SCRIPT)
    _, events = speak(tracker, [chunks[0]] + chunks[2:6])
    assert tracker.skipped == {1}, (tracker.skipped, events)
    assert any(e.startswith("Jumped ahead") for e in events), events


def test_starting_at_part_2_skips_part_1(tracker):
    chunks = chunk_script(SCRIPT)
    _, events = speak(tracker, chunks[1:4])
    assert 0 in tracker.skipped, events


def test_on_topic_speech_matching_no_part_is_off_script():
    # a stand-in embedding: each line its own direction, everything else the script's overall
    # topic, so new speech is on topic (similarity 1) but matches no line (~0.32 < 0.35)
    import numpy as np
    chunks = chunk_script(SCRIPT)
    whole = " ".join(chunks)
    n = len(chunks)

    def embed(texts):
        out = []
        for text in texts:
            v = np.zeros(n)
            if text in chunks:
                v[chunks.index(text)] = 1
            else:                                   # the whole script, or anything said
                v[:] = 1 / np.sqrt(n)
            out.append(v)
        return np.array(out)

    t = ScriptTracker(chunks, embed)
    assert whole and t.topic_vec is not None
    _, events = speak(t, ["zebra quilt marmalade " * 12])     # no keyword overlap with the script
    assert any(e.startswith("Off script") for e in events), events

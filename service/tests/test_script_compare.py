"""Script comparison in the service: the matching itself (same code as the experiment) and how the
audio side feeds it the existing transcript."""

import numpy as np
import pytest

from presentation_service.audio_analysis import AudioAnalyzer
from presentation_service.script_compare import Embedder, ScriptTracker, chunk_script
from presentation_service.server import AlertBoard
from presentation_service.transcript import load_moonshine
from tests.test_audio import SR, feed_all, say

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
CHUNKS = chunk_script(SCRIPT)


@pytest.fixture(scope="module")
def embed():
    return Embedder()


@pytest.fixture(scope="module")
def stt_model():
    return load_moonshine()


class RecordingBoard(AlertBoard):
    """Keeps every pop-up posted (the real board expires them after 2.5 s)."""

    def __init__(self):
        super().__init__()
        self.posted = []

    def post(self, text):
        self.posted.append(text)
        super().post(text)


def analyzer(stt_model, embed, board=None):
    a = AudioAnalyzer(stt_model, board or AlertBoard())
    a.set_embedder(embed)
    return a


def feed_realtime(a, samples, chunk=2048, monkeypatch=None):
    """Like feed_all, but advances the clock the script check uses by the audio's duration."""
    import presentation_service.audio_analysis as aa
    clock = [0.0]
    real = aa.time.monotonic
    aa.time.monotonic = lambda: clock[0]
    try:
        out = []
        for i in range(0, len(samples), chunk):
            clock[0] += chunk / SR
            out += a.feed(samples[i:i + chunk].astype(np.float32))
        return out
    finally:
        aa.time.monotonic = real


def test_tracker_same_behaviour_as_experiment(embed):
    t = ScriptTracker(CHUNKS, embed)
    clock, said, positions = 0.0, [], []
    for w in " ".join(CHUNKS).split():
        said.append(w)
        if len(said) % 8 == 0:
            clock += 1.5
            m = t.update(" ".join(said), now=clock)
            positions.append(m.position)
    assert positions[-1] >= len(CHUNKS) - 2 and all(b >= a for a, b in zip(positions, positions[1:]))


def test_no_script_means_no_script_messages(stt_model, embed):
    a = analyzer(stt_model, embed)
    assert a.set_script("") == {"type": "script_loaded", "chunks": [], "ready": False}
    msgs = feed_all(a, np.zeros(SR, dtype=np.float32))
    assert not [m for m in msgs if m["type"].startswith("script")]


def test_script_loaded_reports_parts(stt_model, embed):
    msg = analyzer(stt_model, embed).set_script(SCRIPT)
    assert msg["ready"] and len(msg["chunks"]) == 10


def test_script_arriving_before_model_starts_once_model_is_ready(stt_model, embed):
    a = AudioAnalyzer(stt_model, AlertBoard())
    assert a.set_script(SCRIPT)["ready"] is False
    a.set_embedder(embed)
    msgs = feed_all(a, np.zeros(SR // 2, dtype=np.float32))
    assert {"type": "script_loaded", "chunks": CHUNKS, "ready": True} in msgs


def test_skipping_ahead_in_speech_is_tracked_and_alerted(stt_model, embed, tmp_path):
    # reads parts 1-3, then skips to part 7: tracked from the transcript the service already makes
    speech = say(" ".join(CHUNKS[:3] + CHUNKS[6:9]), tmp_path)
    board = RecordingBoard()
    a = analyzer(stt_model, embed, board)
    a.set_script(SCRIPT)
    msgs = feed_realtime(a, np.concatenate([speech, np.zeros(SR, dtype=np.float32)]))
    msgs += a.finish()
    positions = [m for m in msgs if m["type"] == "script_position"]
    assert positions and positions[-1]["position"] >= 7
    assert {3, 4, 5} <= set(positions[-1]["skipped"])
    assert any(e.startswith("Jumped ahead to chunk 7") for m in positions for e in m["events"])
    assert "Skipped ahead" in board.posted
    assert not any(any(ch.isdigit() for ch in p) for p in board.posted)     # pop-ups never name a part
    assert a.script_order_events == 1                                     # counted for the score


def test_disabled_rule_hides_popup_but_keeps_tracking(stt_model, embed, tmp_path):
    speech = say(" ".join(CHUNKS[:3] + CHUNKS[6:9]), tmp_path)
    board = RecordingBoard()
    a = analyzer(stt_model, embed, board)
    a.set_script(SCRIPT)
    a.apply_settings({"out_of_order": {"enabled": False, "threshold": None, "duration": 0, "cooldown": 0}})
    msgs = feed_realtime(a, np.concatenate([speech, np.zeros(SR, dtype=np.float32)])) + a.finish()
    assert any(m["type"] == "script_position" and m["skipped"] for m in msgs)
    assert not any(x.startswith("Skipped") for x in board.posted)


def test_off_script_slider_and_stuck_factor_reach_the_tracker(stt_model, embed):
    a = analyzer(stt_model, embed)
    a.set_script(SCRIPT)
    a.apply_settings({"off_script": {"enabled": True, "threshold": 50, "duration": 10, "cooldown": 0},
                      "stuck": {"enabled": True, "threshold": 5, "duration": 0, "cooldown": 0}})
    t = a.script_tracker
    assert (t.threshold, t.off_topic_s, t.stuck_factor) == (0.5, 10.0, 5.0)


def test_new_session_keeps_script_but_starts_from_the_top(stt_model, embed):
    a = analyzer(stt_model, embed)
    a.set_script(SCRIPT)
    a.script_tracker.position = 5
    a.reset()
    assert a.script_chunks == CHUNKS and a.script_tracker.position == -1


def test_tracking_starting_mid_session_does_not_mark_earlier_parts_skipped(stt_model, embed, tmp_path):
    # the model "finishes loading" after three parts were already said
    speech = say(" ".join(CHUNKS[:3]), tmp_path)
    a = AudioAnalyzer(stt_model, RecordingBoard())
    a.set_script(SCRIPT)
    feed_realtime(a, speech)
    a.set_embedder(embed)
    msgs = feed_realtime(a, np.zeros(SR, dtype=np.float32)) + a.finish()
    last = [m for m in msgs if m["type"] == "script_position"][-1]
    assert last["position"] >= 1 and last["skipped"] == []        # parts 1-2 visited, not skipped


def test_position_follows_the_sentence_being_said(embed):
    t = ScriptTracker(CHUNKS, embed)
    said = (CHUNKS[0] + " " + CHUNKS[1]).split()
    t.update(" ".join(said), now=1.0)
    t.update(" ".join(said), now=3.0)
    half = CHUNKS[2].split()[:7]                        # under half of part 3 is enough to be on it
    assert t.update(" ".join(said + half), now=5.0).position == 2


def test_catching_up_marks_what_was_said_and_does_not_alert(embed):
    t = ScriptTracker(CHUNKS, embed)
    t.catch_up(" ".join(CHUNKS[:4] + CHUNKS[6:8]), now=100.0)
    assert t.position >= 7 and {4, 5} <= t.skipped and 1 in t.visited
    assert t.off_since is None and t.entered_at == 100.0


def test_a_script_part_of_only_fillers_or_symbols_is_tolerated(embed):
    t = ScriptTracker(["Um, so, like.", "*** !!! ***", "The results are good this year."], embed)
    assert t.update("and the results are good this year", now=1.0) is not None


def test_editing_the_script_only_embeds_what_changed(embed):
    embedded = []

    def counting(texts):
        embedded.extend(texts)
        return embed(texts)

    first = ScriptTracker(CHUNKS, counting)
    everything, edited = len(embedded), CHUNKS[:-1] + ["Thank you, and have a wonderful evening everyone."]
    embedded.clear()
    second = ScriptTracker(edited, counting, known=first.vectors)
    assert len(embedded) < everything / 3
    assert np.allclose(second.chunk_vecs, ScriptTracker(edited, embed).chunk_vecs, atol=1e-3)

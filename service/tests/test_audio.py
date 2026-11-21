import asyncio
import json
import shutil
import struct
import subprocess
import wave

import numpy as np
import pytest

from presentation_service.audio_analysis import AudioAnalyzer
from presentation_service.scoring import BODY_RULES, VOICE_RULES, score_presentation
from presentation_service.server import AlertBoard, Session, audio_connection
from presentation_service.transcript import load_moonshine

SR = 16000


@pytest.fixture(scope="module")
def stt_model():
    return load_moonshine()


def feed_all(analyzer, samples, chunk=2048):
    """Feeds audio in Electron-sized chunks; returns every message sent back."""
    out = []
    for i in range(0, len(samples), chunk):
        out += analyzer.feed(samples[i:i + chunk].astype(np.float32))
    return out


def say(text, tmp_path):
    """macOS text-to-speech -> 16 kHz float32 samples."""
    if not shutil.which("say"):
        pytest.skip("needs macOS `say`")
    path = tmp_path / "speech.wav"
    subprocess.run(["say", "-o", str(path), "--data-format=LEI16@16000", text], check=True)
    with wave.open(str(path)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def test_tone_gives_pitch_and_volume(stt_model):
    t = np.arange(SR * 2) / SR
    tone = 0.2 * np.sin(2 * np.pi * 200 * t)
    msgs = [m for m in feed_all(AudioAnalyzer(stt_model, AlertBoard()), tone) if m["type"] == "voice"]
    assert len(msgs) == 20                                      # every 100 ms
    assert all(len(m["pitches"]) == 2 for m in msgs)            # both 50 ms readings in each
    assert abs(msgs[-1]["pitch_hz"] - 200) < 5 and -20 < msgs[-1]["volume_dbfs"] < -10
    # after ~1 s of voice the running typical pitch exists; a spike fires 9 semitones above it
    assert msgs[0]["typical_hz"] is None and abs(msgs[-1]["typical_hz"] - 200) < 5
    assert abs(msgs[-1]["spike_hz"] - msgs[-1]["typical_hz"] * 2 ** (9 / 12)) < 0.5


def test_typical_pitch_follows_the_last_10_seconds_of_speech(stt_model):
    a = AudioAnalyzer(stt_model, AlertBoard())
    a.set_noise_floor(-70.0)
    tone = lambda hz, s: (0.2 * np.sin(2 * np.pi * hz * np.arange(int(SR * s)) / SR)).astype(np.float32)  # noqa: E731
    silence = lambda s: np.zeros(int(SR * s), dtype=np.float32)                                             # noqa: E731

    msgs = feed_all(a, tone(150, 4))
    assert abs(msgs[-1]["typical_hz"] - 150) < 5
    # a long pause doesn't count: the window is 10 s of *speech*, so 150 Hz is still typical after it
    msgs = feed_all(a, silence(20))
    assert abs(msgs[-1]["typical_hz"] - 150) < 5
    # 4 s at 150 + 4 s at 220 fit in the window together; the median sits between them
    msgs = feed_all(a, tone(220, 4))
    assert 140 < msgs[-1]["typical_hz"] < 230
    # after more than 10 s at 220 Hz, the old 150 Hz speech has left the window
    msgs = feed_all(a, tone(220, 8))
    assert abs(msgs[-1]["typical_hz"] - 220) < 5
    assert "calibrated" not in msgs[-1]                 # no voice calibration anymore


def test_quiet_alert_posts_to_board(stt_model):
    board = AlertBoard()
    feed_all(AudioAnalyzer(stt_model, board), np.zeros(SR * 4, dtype=np.float32))
    assert "Speak up" in board.active()


def test_fillers_are_scored_per_minute():
    voice = dict.fromkeys(VOICE_RULES, 0)
    body = dict.fromkeys(BODY_RULES, 0)
    # 8 fillers in one minute is at the "bad" rate (8/min): the full 6 filler points are lost.
    # Pace and volume weren't measured, so the 20 measured points are scaled to the 28-point rubric.
    result = score_presentation(filler_counts={"um": 8, "filler?": 5},
                                voice_incidents=voice, body_incidents=body, duration_seconds=60)
    assert result["max_score"] == 28
    assert result["measured_points"] == 20
    assert result["categories"]["filler"]["deduction"] == 6
    assert result["score"] == pytest.approx(28 * 14 / 20)
    assert result["categories"]["voice_tone"]["max_points"] == 3
    assert result["categories"]["body_language"]["max_points"] == 11
    # the same 8 fillers over 10 minutes is under the "good" rate: nothing lost
    relaxed = score_presentation(filler_counts={"um": 8}, voice_incidents=voice, body_incidents=body,
                                 duration_seconds=600)
    assert relaxed["categories"]["filler"]["deduction"] == 0


def test_pace_scores_moderate_delivery_and_consistency():
    steady = score_presentation(pace={"average_wpm": 140, "segment_wpms": [135, 145, 140]})
    assert steady["categories"]["speaking_pace"]["points"] == 4
    assert steady["max_score"] == 28
    assert steady["score"] == 28

    rushed = score_presentation(pace={"average_wpm": 210, "segment_wpms": [100, 190, 240]})
    checks = rushed["categories"]["speaking_pace"]["checks"]
    assert checks["moderate_pace"]["deduction"] == 2
    assert checks["consistent_pace"]["deduction"] > 0


def test_pace_is_not_scored_without_enough_transcript_data():
    assert score_presentation(pace=None)["categories"]["speaking_pace"]["points"] is None


def test_current_wpm_uses_recent_timed_words(stt_model):
    analyzer = AudioAnalyzer(stt_model, AlertBoard())
    analyzer.transcriber.word_times = [(i * 0.5, i * 0.5 + 0.2) for i in range(20)]
    assert analyzer.current_wpm() == pytest.approx(123.7, abs=0.1)


def test_live_wpm_uses_partial_transcript_history(stt_model):
    analyzer = AudioAnalyzer(stt_model, AlertBoard())
    analyzer.transcriber.pace_history.extend([(0, 0), (4, 8), (8, 18)])
    analyzer.transcriber.last_voiced_at = 8
    assert analyzer.transcriber.live_wpm() == 135


def test_live_wpm_is_unavailable_during_a_pause(stt_model):
    analyzer = AudioAnalyzer(stt_model, AlertBoard())
    analyzer.transcriber.pace_history.extend([(0, 0), (4, 8), (8, 18)])
    analyzer.transcriber.last_voiced_at = 6
    assert analyzer.transcriber.live_wpm() is None


def test_final_pace_excludes_a_presentation_pause(stt_model):
    analyzer = AudioAnalyzer(stt_model, AlertBoard())
    # Two ten-word phrases at the same rate, separated by a five-second pause.
    # The pause must not be counted as slow speech in the final pace score.
    analyzer.transcriber.word_times = [
        *((i * 0.5, i * 0.5 + 0.2) for i in range(10)),
        *((10 + i * 0.5, 10 + i * 0.5 + 0.2) for i in range(10)),
    ]
    assert analyzer.pace_summary()["average_wpm"] == pytest.approx(127.7, abs=0.1)


@pytest.mark.parametrize(
    ("seconds_per_word", "expected_alert"),
    [
        (0.7, "Pick up the pace"),
        (0.25, "Slow down"),
    ],
)
def test_out_of_range_pace_posts_the_matching_live_alert(
    stt_model, seconds_per_word, expected_alert
):
    board = AlertBoard()
    analyzer = AudioAnalyzer(stt_model, board)
    # Forty words provide enough active speech for a reliable live estimate.
    analyzer.transcriber.word_times = [
        (i * seconds_per_word, i * seconds_per_word + 0.1) for i in range(40)
    ]
    analyzer.check_pace()
    assert expected_alert in board.active()


def test_session_score_has_recording_relative_checkpoints(stt_model):
    session = Session(stt_model)
def test_session_score_has_recording_relative_checkpoints(stt_model):
    session = Session(stt_model)
    session.audio.set_embedder(lambda texts: np.ones((len(texts), 1), dtype=np.float32))

    class Client:
        sent = []

        async def __aiter__(self):
            yield json.dumps({"type": "script", "text": "Results improved across all markets."})
            yield json.dumps({"type": "session_start", "started_at": 50})
            # One sustained camera incident should give one marker at the onset.
            for i in range(30):
                session.webcam.process({"t": 50.5 + i / 10, "width": 640, "height": 480})

            # Simulate one dropped mic chunk before a filler in the final phrase.
            yield struct.pack("<d", 51) + np.zeros(SR // 10, dtype="<f4").tobytes()
            yield struct.pack("<d", 51.6) + np.zeros(3 * SR, dtype="<f4").tobytes()
            # Moonshine reports a finished line with its start time (seconds of speech audio)
            transcriber = session.audio.transcriber
            transcriber.flush = lambda: (
                transcriber._on_final("you know results improved across all markets", 0.35),
                transcriber.drain(),
            )[1]
            yield json.dumps({"type": "session_end"})

        async def send(self, message):
            self.sent.append(json.loads(message))

    client = Client()
    with session.audio_worker:
        asyncio.run(audio_connection(client, session))
    readings = [message for message in client.sent if message["type"] == "voice"]
    assert readings[0]["captured_at"] == pytest.approx(51.1 - 1 / SR)
    assert readings[1]["captured_at"] == pytest.approx(51.7 - 1 / SR)
    assert client.sent[0]["type"] == "script_loaded" and client.sent[0]["ready"]
    transcript, script_position, report = client.sent[-3:]
    assert transcript["fillers"]["you know"] == 1
    assert script_position["type"] == "script_position"
    assert report["type"] == "session_score"
    assert report["checkpoints"] == [
        {"at": 0.5, "category": "body_language", "label": "Face the camera"},
        {"at": 1.0, "category": "voice_tone", "label": "Speak up"},
        {"at": 1.85, "category": "filler", "label": "Filler: you know"},
    ]


def test_transcript_and_filler_counts_from_speech(stt_model, tmp_path):
    speech = say("So I think, you know, the results are like really good. So that is the plan.", tmp_path)
    board = AlertBoard()
    a = AudioAnalyzer(stt_model, board)
    msgs = feed_all(a, np.concatenate([speech, np.zeros(SR, dtype=np.float32)]))
    msgs += a.finish()
    transcripts = [m for m in msgs if m["type"] == "transcript"]
    sentences = [s for m in transcripts for s in m["new_sentences"]]
    text = " ".join(sentences).lower()
    assert "results" in text and "plan" in text
    assert any(m["partial"] for m in transcripts)               # running words while "talking"
    final = transcripts[-1]
    assert final["fillers"]["you know"] >= 1 and final["fillers"]["so"] >= 1 and final["fillers"]["like"] >= 1
    assert final["total"] == sum(final["fillers"].values())
    assert {"Filler: you know", "Filler: like", "Filler: so"} <= set(board.active())
    print("\n", sentences, final["fillers"])


def test_filler_counts_go_up_before_the_sentence_ends(stt_model, tmp_path):
    speech = say("I really like this idea and I like the plan", tmp_path)       # no pause: stays partial
    a = AudioAnalyzer(stt_model, AlertBoard())
    msgs = [m for m in feed_all(a, speech) if m["type"] == "transcript"]
    assert not any(m["new_sentences"] for m in msgs)                           # sentence not finished yet
    assert msgs[-1]["fillers"]["like"] == 2 and msgs[-1]["total"] == 2
    final = a.finish()[0]
    assert final["fillers"]["like"] == 2                                       # not double-counted at the end


def voice_rules(**changes):
    base = {r: {"enabled": True, "threshold": th, "duration": d, "cooldown": 3}
            for r, th, d in (("volume", 8, 0.3), ("pitch", 9, 0.2), ("quiet", 15, 3))}
    base["filler"] = {"enabled": True, "threshold": None, "duration": 0, "cooldown": 0}
    for rule_id, change in changes.items():
        base[rule_id] = {**base[rule_id], **change}
    return base


def test_voice_settings_disable_and_retime_quiet_alert(stt_model):
    silence = np.zeros(SR * 4, dtype=np.float32)
    board = AlertBoard()
    a = AudioAnalyzer(stt_model, board)
    a.apply_settings(voice_rules(quiet={"enabled": False}))
    feed_all(a, silence)
    assert "Speak up" not in board.active()

    board = AlertBoard()
    a = AudioAnalyzer(stt_model, board)
    a.apply_settings(voice_rules(quiet={"duration": 10}))       # longer than the 4 s of silence
    feed_all(a, silence)
    assert "Speak up" not in board.active()


def test_voice_threshold_and_settings_survive_new_session(stt_model):
    a = AudioAnalyzer(stt_model, AlertBoard())
    a.apply_settings(voice_rules(pitch={"threshold": 4}, volume={"threshold": 12}))
    a.reset()                                                    # new practice session
    assert a.voice.pitch_jump_semitones == 4 and a.voice.volume_jump_db == 12


def test_filler_alert_follows_rule_but_still_counts(stt_model, tmp_path):
    speech = say("I like this and I like that", tmp_path)
    board = AlertBoard()
    a = AudioAnalyzer(stt_model, board)
    a.apply_settings(voice_rules(filler={"enabled": False}))
    msgs = feed_all(a, speech) + a.finish()
    assert msgs[-1]["fillers"]["like"] == 2 and not any(x.startswith("Filler") for x in board.active())


def test_pace_from_real_speech_with_moonshine(stt_model, tmp_path):
    # ~28 words of TTS speech, then 2 s of silence
    speech = say("Today I will walk you through our sales results, our hiring plans, and the new "
                 "product launch. First, sales grew by twelve percent compared to last quarter.", tmp_path)
    a = AudioAnalyzer(stt_model, AlertBoard())
    live = []
    for i in range(0, len(speech), 2048):
        a.feed(speech[i:i + 2048].astype(np.float32))
        live.append(a.live_wpm())
    readings = [w for w in live if w is not None]
    assert readings and 80 < readings[-1] < 260            # live WPM while talking (partials)
    feed_all(a, np.zeros(SR * 2, dtype=np.float32))
    assert a.live_wpm() is None                            # paused: no live reading
    a.finish()
    t = a.transcriber
    assert len(t.word_times) >= 20                         # finished lines spread into word times
    assert all(s < e for s, e in t.word_times)
    summary = a.pace_summary()
    assert summary and 80 < summary["average_wpm"] < 260
    print("\nlive WPM readings:", readings[-5:], "final:", summary)


def test_room_silence_level_separates_pauses_from_quiet_speech(stt_model):
    t = np.arange(SR * 5) / SR
    room = (0.003 * np.random.default_rng(0).standard_normal(len(t))).astype(np.float32)   # ~-50 dBFS hiss
    quiet_voice = (0.006 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)                 # ~-47 dBFS

    # without a measured silence level, room hiss alone counts as "too quiet" -> Speak up
    board = AlertBoard()
    feed_all(AudioAnalyzer(stt_model, board), room)
    assert "Speak up" in board.active()

    # with it: the same hiss is just "not speaking" (a pause), no alert...
    board = AlertBoard()
    a = AudioAnalyzer(stt_model, board)
    a.set_noise_floor(-50.0)
    msgs = [m for m in feed_all(a, room) if m["type"] == "voice"]
    assert "Speak up" not in board.active()
    assert not msgs[-1]["speaking"] and msgs[-1]["silence_dbfs"] == -40.0

    # ...while speaking above the room level but quietly still counts as speaking quietly
    a = AudioAnalyzer(stt_model, AlertBoard())
    a.set_noise_floor(-60.0)
    msgs = [m for m in feed_all(a, quiet_voice) if m["type"] == "voice"]
    assert msgs[-1]["speaking"] and msgs[-1]["pitch_hz"]


def test_silence_level_survives_a_new_session_and_is_clamped(stt_model):
    a = AudioAnalyzer(stt_model, AlertBoard())
    a.set_noise_floor(-55.0)
    a.reset()
    assert a.voice.silence_dbfs == -45.0
    a.set_noise_floor(-5.0)                        # a bad measurement (someone talked) is capped
    assert a.voice.silence_dbfs == -30.0


class PostedBoard(AlertBoard):
    """Keeps every pop-up posted (the real board expires them)."""

    def __init__(self):
        super().__init__()
        self.posted = []

    def post(self, text):
        self.posted.append(text)


def test_long_pause_alerts_once_after_the_speaker_starts_talking(stt_model):
    rng = np.random.default_rng(0)
    room = lambda s: (rng.normal(0, 10 ** (-63 / 20), int(SR * s))).astype(np.float32)   # noqa: E731
    tone = lambda s: (0.2 * np.sin(2 * np.pi * 160 * np.arange(int(SR * s)) / SR)).astype(np.float32)  # noqa: E731

    def with_clicks(audio):
        # noise bursts well above the silence level (like the spikes on the volume graph)
        for at in (1.0, 1.3, 2.5):
            i = int(SR * at)
            audio[i:i + 400] += rng.normal(0, 0.02, 400).astype(np.float32)
        return audio

    board = PostedBoard()
    a = AudioAnalyzer(stt_model, board)
    a.set_noise_floor(-64.0)                            # not speaking below -54 dBFS
    feed_all(a, room(8))                                # before the first word: no pause alert
    assert "Keep talking" not in board.posted
    feed_all(a, tone(3))
    feed_all(a, room(2.5))                              # a short, deliberate pause is fine
    feed_all(a, tone(2))
    assert "Keep talking" not in board.posted
    feed_all(a, with_clicks(room(8)))                   # long pause; clicks don't reset the timer
    assert board.posted.count("Keep talking") == 1      # once per pause, ~5 s in
    feed_all(a, tone(2))
    feed_all(a, room(6))
    assert board.posted.count("Keep talking") == 2      # the next long pause alerts again

    # the Live alerts rule: turned off -> no pop-up; its minimum duration is the pause length
    board = PostedBoard()
    a = AudioAnalyzer(stt_model, board)
    a.set_noise_floor(-64.0)
    a.apply_settings({"pause": {"enabled": True, "duration": 10, "cooldown": 0}})
    feed_all(a, tone(2))
    feed_all(a, room(8))
    assert "Keep talking" not in board.posted           # 8 s < 10 s
    a.apply_settings({"pause": {"enabled": False, "duration": 3, "cooldown": 0}})
    feed_all(a, tone(2))
    feed_all(a, room(6))
    assert "Keep talking" not in board.posted


def test_volume_scores_projection_steadiness_and_long_pauses():
    loud = score_presentation(volume={"speech_dbfs": -30, "segment_dbfs": [-30, -31, -29], "room_dbfs": -64},
                              voice_incidents={"pause": 0})
    v = loud["categories"]["volume"]
    assert v["points"] == 4 and v["max_points"] == 4 and v["checks"]["projection"]["margin_db"] == 34

    # barely above the room, trailing off, and three long pauses
    weak = score_presentation(volume={"speech_dbfs": -47, "segment_dbfs": [-38, -46, -56], "room_dbfs": -64},
                              voice_incidents={"pause": 3})
    checks = weak["categories"]["volume"]["checks"]
    assert 0 < checks["projection"]["deduction"] < 2          # 17 dB above the room: partial credit
    assert 0.5 < checks["steady_volume"]["deduction"] < 1      # ~7 dB spread between the parts
    assert "quieter as you went on" in checks["steady_volume"]["feedback"]
    assert checks["long_pause"]["deduction"] == 1             # capped

    # no room measurement and under 20 s of speech: only long pauses are scored
    partial = score_presentation(volume={"speech_dbfs": -30, "segment_dbfs": [-30], "room_dbfs": None},
                                 voice_incidents={"pause": 0})
    assert list(partial["categories"]["volume"]["checks"]) == ["long_pause"]


def test_analyzer_volume_summary_from_speech(stt_model):
    a = AudioAnalyzer(stt_model, AlertBoard())
    a.set_noise_floor(-64.0)
    tone = lambda amp, s: (amp * np.sin(2 * np.pi * 160 * np.arange(int(SR * s)) / SR)).astype(np.float32)  # noqa: E731
    feed_all(a, tone(0.2, 3))
    assert a.volume_summary() is None                         # under 5 s of speech
    feed_all(a, np.zeros(SR * 10, dtype=np.float32))         # silence doesn't count toward it
    feed_all(a, tone(0.2, 9))
    feed_all(a, tone(0.05, 12))                               # quieter second half
    s = a.volume_summary()
    assert s["room_dbfs"] == -64.0 and len(s["segment_dbfs"]) == 2
    assert s["segment_dbfs"][0] - s["segment_dbfs"][1] > 10
    assert a.voice_incidents["pause"] >= 1                    # the 10 s silence after speaking


def test_finished_lines_are_recorded_for_the_average_pace(stt_model, tmp_path):
    a = AudioAnalyzer(stt_model, AlertBoard())
    feed_all(a, say("Good afternoon everyone. Thank you for coming today.", tmp_path))
    feed_all(a, np.zeros(SR * 2, dtype=np.float32))
    a.finish()
    lines = a.transcriber.lines
    assert lines and all(end > start for start, end, _ in lines)
    assert sum(words for _, _, words in lines) == len(a.transcriber.word_times)


def test_average_wpm_counts_the_pauses_between_the_last_three_lines(stt_model):
    a = AudioAnalyzer(stt_model, AlertBoard())
    a.transcriber.lines = [(0, 4, 10), (5, 9, 10)]
    assert a.average_wpm() is None                          # needs 3 lines
    a.transcriber.lines.append((10, 14, 10))
    assert a.average_wpm() == pytest.approx(30 * 60 / 14, abs=0.1)
    a.transcriber.lines.append((30, 34, 10))                # only the last 3 count
    assert a.average_wpm() == pytest.approx(30 * 60 / 29, abs=0.1)


def test_desired_pace_from_script_and_time_else_the_slider(stt_model):
    a = AudioAnalyzer(stt_model, AlertBoard())
    assert a.desired_wpm() == 140 and a.desired_source() == "setting"
    a.set_target_time(600)                                  # time but no script: still the slider
    assert a.desired_wpm() == 140
    a.set_script(" ".join(["word"] * 1300))
    msg = a.pace_target_message()
    assert msg["desired_wpm"] == 130 and msg["source"] == "script" and msg["script_words"] == 1300
    a.set_target_time(None)
    a.apply_settings({"pace": {"enabled": True, "threshold": 160, "duration": 0, "cooldown": 5}})
    assert a.desired_wpm() == 160 and a.desired_source() == "setting"


@pytest.mark.parametrize(("words_per_line", "expected"), [
    (5, ["Falling behind: speed up"]),   # 15 words / 9 s = 100 WPM: 23% under 130
    (7, []),                             # 140 WPM: within 15%
    (8, ["Too fast: slow down"]),        # 160 WPM: 23% over
])
def test_average_pace_alert_both_ways(stt_model, words_per_line, expected):
    board = PostedBoard()
    a = AudioAnalyzer(stt_model, board)
    a.set_script(" ".join(["word"] * 1300))
    a.set_target_time(600)                                  # desired 130 WPM
    a.transcriber.lines = [(0, 2, words_per_line), (3.5, 5.5, words_per_line), (7, 9, words_per_line)]
    a.check_average_pace()
    assert board.posted == expected


def test_average_pace_alert_tolerance_cooldown_and_off_switch(stt_model):
    board = PostedBoard()
    a = AudioAnalyzer(stt_model, board)
    a.set_script(" ".join(["word"] * 1300))
    a.set_target_time(600)                                  # desired 130 WPM
    a.transcriber.lines = [(0, 2, 6), (3.5, 5.5, 6), (7, 9, 6)]     # 18 words / 9 s = 120 WPM
    a.check_average_pace()
    assert board.posted == []                               # within 15%
    a.transcriber.lines = [(0, 2, 5), (3.5, 5.5, 5), (7, 9, 5)]     # 100 WPM
    a.apply_settings({"pace_avg": {"enabled": True, "threshold": 30, "duration": 0, "cooldown": 15}})
    a.check_average_pace()
    assert board.posted == []                               # 23% under, but the slider allows 30%
    a.apply_settings({"pace_avg": {"enabled": True, "threshold": 15, "duration": 0, "cooldown": 15}})
    a.check_average_pace()
    a.check_average_pace()
    assert board.posted == ["Falling behind: speed up"]     # once: cooldown
    board.posted.clear()
    a.last_alert.clear()
    a.apply_settings({"pace_avg": {"enabled": False, "threshold": 15, "duration": 0, "cooldown": 0}})
    a.check_average_pace()
    assert board.posted == []


def test_live_pace_alert_uses_the_desired_pace(stt_model):
    board = PostedBoard()
    a = AudioAnalyzer(stt_model, board)
    a.set_script(" ".join(["word"] * 2000))
    a.set_target_time(600)                                  # desired 200 WPM
    # ~130 WPM of timed words: fine against the 140 slider, but more than 30% under 200
    a.transcriber.word_times = [(i * 0.46, i * 0.46 + 0.1) for i in range(40)]
    a.check_pace()
    assert "Pick up the pace" in board.posted

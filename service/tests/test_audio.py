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


def test_voice_calibration_from_speechlike_tone(stt_model):
    a = AudioAnalyzer(stt_model, AlertBoard())
    a.request_calibration()
    t = np.arange(SR * 4) / SR
    msgs = feed_all(a, 0.2 * np.sin(2 * np.pi * 180 * t))
    assert msgs[-1]["calibrated"] and "Voice calibrated: 180 Hz" in msgs[-1]["message"]


def test_quiet_alert_posts_to_board(stt_model):
    board = AlertBoard()
    feed_all(AudioAnalyzer(stt_model, board), np.zeros(SR * 4, dtype=np.float32))
    assert "Speak up" in board.active()


def test_repeated_fillers_cost_more_in_twenty_four_point_rubric():
    voice = dict.fromkeys(VOICE_RULES, 0)
    body = dict.fromkeys(BODY_RULES, 0)
    result = score_presentation(filler_counts={"um": 8, "filler?": 5},
                                voice_incidents=voice, body_incidents=body)
    # Pace was not available, so the existing checks are normalized to the
    # full 24-point rubric while measured_points reports what was assessed.
    assert result["max_score"] == 24
    assert result["measured_points"] == 20
    assert result["score"] == 18
    assert result["categories"]["voice_tone"]["max_points"] == 3
    assert result["categories"]["body_language"]["max_points"] == 11
    assert score_presentation(filler_counts={"um": 100})["score"] == 0


def test_pace_scores_moderate_delivery_and_consistency():
    steady = score_presentation(pace={"average_wpm": 140, "segment_wpms": [135, 145, 140]})
    assert steady["categories"]["speaking_pace"]["points"] == 4
    assert steady["max_score"] == 24
    assert steady["score"] == 24

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

import asyncio
import json
import shutil
import struct
import subprocess
import wave
from unittest.mock import Mock

import numpy as np
import pytest

from presentation_service.audio_analysis import AudioAnalyzer
from presentation_service.scoring import BODY_RULES, VOICE_RULES, score_presentation
from presentation_service.server import AlertBoard, Session, audio_connection, voice_calibration_connection
from presentation_service.transcript import load_moonshine
from presentation_service.voice import BLOCK_SIZE, CALIBRATION_SECONDS, VoiceMonitor, pitch_hz

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
    assert msgs[-1]["typical_hz"] is None and msgs[-1]["spike_hz"] is None
    assert msgs[-1]["typical_volume_dbfs"] is None


@pytest.mark.parametrize(("hz", "amp", "rule", "banner"), [
    (130, 0.2, "pitch", "Steady your pitch"),
    (300, 0.2, "pitch", "Steady your pitch"),
    (200, 0.8, "volume", "Steady your volume"),          # 12 dB above the -17 dBFS reference
    (200, 0.06, "quiet", "Speak up"),
])
def test_sustained_voice_violations_alert_and_lose_credit(hz, amp, rule, banner):
    board = AlertBoard()
    analyzer = AudioAnalyzer(Mock(), board)
    analyzer.reset((200, -17))
    analyzer.set_noise_floor(-65.0)

    def tone(frequency, amplitude, seconds):
        return amplitude * np.sin(2 * np.pi * frequency * np.arange(int(SR * seconds)) / SR)

    feed_all(analyzer, tone(200, 0.2, 2))
    messages = feed_all(analyzer, tone(hz, amp, 0.8))
    assert banner in board.active()
    assert any(banner in msg.get("alerts", []) for msg in messages)
    assert analyzer.voice_incidents[rule] == 1
    report = score_presentation(voice_incidents=analyzer.voice_incidents, duration_seconds=600)
    # pitch: 1.0 of its 5 points per incident; volume / quiet: 0.75 of 5 = 3.75, shown as 3.8
    assert report["categories"]["voice_tone"]["checks"][rule]["deduction"] == (5 if rule == "pitch" else 3.8)

    # Another failure during the banner cooldown still counts in the results.
    feed_all(analyzer, tone(200, 0.2, 0.2))
    feed_all(analyzer, tone(hz, amp, 0.8))
    assert analyzer.voice_incidents[rule] == 2
    report = score_presentation(voice_incidents=analyzer.voice_incidents, duration_seconds=600)
    assert report["categories"]["voice_tone"]["checks"][rule]["deduction"] == 5   # all 5 points


def test_pitch_estimator_prefers_fundamental_over_a_louder_third_harmonic():
    phase = 2 * np.pi * 127 * np.arange(SR // 20) / SR
    assert pitch_hz(0.04 * np.sin(phase) + 0.15 * np.sin(3 * phase)) == pytest.approx(127, abs=2)


def test_short_pitch_swings_count_across_unvoiced_gaps_and_direction_changes():
    analyzer = AudioAnalyzer(Mock(), AlertBoard())
    analyzer.reset((200, -17))
    analyzer.voice.pitch_spike.duration_seconds = 0.1
    time = np.arange(SR // 20) / SR
    high = 0.2 * np.sin(2 * np.pi * 300 * time)
    low = 0.2 * np.sin(2 * np.pi * 130 * time)
    samples = np.concatenate([high, np.zeros_like(high), high, low, low])
    feed_all(analyzer, samples)
    assert analyzer.voice_incidents["pitch"] == 2


def test_isolated_high_pitch_jumps_are_filtered_but_sustained_high_pitch_counts():
    analyzer = AudioAnalyzer(Mock(), AlertBoard())
    analyzer.reset((127, -17))
    time = np.arange(SR // 20) / SR
    normal = 0.2 * np.sin(2 * np.pi * 127 * time)
    harmonic = 0.2 * np.sin(2 * np.pi * 381 * time)
    high = 0.2 * np.sin(2 * np.pi * 420 * time)
    short = feed_all(analyzer, np.concatenate([normal, harmonic, normal, high, normal, normal]))
    pitches = [pitch for msg in short if msg["type"] == "voice" for pitch in msg["pitches"]]
    assert pitches[1] is None and pitches[3] is None
    assert analyzer.voice_incidents["pitch"] == 0
    readings = feed_all(analyzer, np.tile(high, 12))
    assert any(msg.get("pitch_hz", 0) and msg["pitch_hz"] > 400 for msg in readings)
    assert analyzer.voice_incidents["pitch"] == 1


def test_voice_calibration_endpoint_measures_speech_and_rejects_silence():
    class Client:
        def __init__(self, samples):
            self.samples = samples
            self.reply = None

        async def __aiter__(self):
            yield self.samples.astype("<f4").tobytes()

        async def send(self, message):
            self.reply = json.loads(message)

    tone = 0.2 * np.sin(2 * np.pi * 200 * np.arange(SR * CALIBRATION_SECONDS) / SR)
    client = Client(tone)
    asyncio.run(voice_calibration_connection(client))
    assert client.reply["baseline"]["pitch_hz"] == pytest.approx(200, abs=3)
    assert client.reply["baseline"]["volume_dbfs"] == pytest.approx(-17, abs=0.1)

    silent = Client(np.zeros_like(tone))
    asyncio.run(voice_calibration_connection(silent))
    assert "microphone level was too low" in silent.reply["error"]

    # A low-gain microphone and short voiced syllables separated by unvoiced sounds.
    rng = np.random.default_rng(6)
    speech_like = rng.normal(0, 0.00012, len(tone))
    for start in (1.0, 1.4, 1.9, 2.2, 2.7, 3.1, 3.4, 3.8):
        offset = round(start * SR)
        length = round(0.14 * SR)
        speech_like[offset:offset + length] += 0.001 * np.hanning(length) * tone[:length] / 0.2
    quiet = Client(speech_like)
    asyncio.run(voice_calibration_connection(quiet))
    assert quiet.reply["baseline"]["pitch_hz"] == pytest.approx(200, abs=5)
    assert -70 <= quiet.reply["baseline"]["volume_dbfs"] < -60

    noise = Client(rng.normal(0, 0.005, len(tone)))
    asyncio.run(voice_calibration_connection(noise))
    assert "clear pitch" in noise.reply["error"]


def test_pitch_and_volume_baseline_does_not_follow_sustained_voice_changes():
    a = AudioAnalyzer(Mock(), AlertBoard())
    a.reset((150, -17))
    tone = 0.8 * np.sin(2 * np.pi * 250 * np.arange(SR * 12) / SR)      # -5 dBFS: 12 dB over -17
    readings = [msg for msg in feed_all(a, tone) if msg["type"] == "voice"]
    assert all(msg["typical_hz"] == 150 and msg["typical_volume_dbfs"] == -17 for msg in readings)
    assert readings[0]["spike_hz"] == pytest.approx(150 * 2 ** (6 / 12), abs=0.1)
    assert a.voice_incidents["pitch"] == 1 and a.voice_incidents["volume"] == 1

    a.reset((250, -11))
    readings = [msg for msg in feed_all(a, tone[:SR]) if msg["type"] == "voice"]
    assert readings[0]["typical_hz"] == 250 and readings[0]["typical_volume_dbfs"] == -11
    assert a.voice_incidents["pitch"] == 0 and a.voice_incidents["volume"] == 0


def test_a_modest_volume_rise_is_not_a_spike():
    # 8 dB above the reference for over a second: under the 10 dB threshold, so no alert
    board = AlertBoard()
    a = AudioAnalyzer(Mock(), board)
    a.reset((200, -17))
    a.set_noise_floor(-65.0)
    feed_all(a, 0.5 * np.sin(2 * np.pi * 200 * np.arange(int(SR * 1.5)) / SR))
    assert a.voice_incidents.get("volume", 0) == 0 and "Steady your volume" not in board.active()


def test_uncalibrated_voice_is_measured_without_pitch_or_volume_deductions():
    board = AlertBoard()
    a = AudioAnalyzer(Mock(), board)
    tone = 0.05 * np.sin(2 * np.pi * 170 * np.arange(SR * 4) / SR)
    feed_all(a, tone)
    assert not {"pitch", "volume", "quiet"} & a.voice_incidents.keys()
    assert "Speak up" not in board.active()


def test_recording_baseline_uses_the_uploaded_audio_not_live_calibration():
    monitor = VoiceMonitor(recording_baseline=True)

    def blocks(hz, amplitude, seconds):
        samples = amplitude * np.sin(2 * np.pi * hz * np.arange(round(SR * seconds)) / SR)
        return [samples[i:i + BLOCK_SIZE] for i in range(0, len(samples), BLOCK_SIZE)]

    # The complete voiced recording establishes its own normal pitch and level.
    for block in blocks(200, 0.02, 1.5):
        monitor.process(block, 0)
    for block in blocks(200, 0.02, 0.5) + blocks(320, 0.2, 0.5):
        monitor.process(block, 0)
    incidents = monitor.finalize_recording_baseline()
    assert monitor.typical_pitch_hz == pytest.approx(200, abs=3)
    assert monitor.typical_volume_dbfs == pytest.approx(-37, abs=1)
    assert monitor.baseline_source == "recording"
    assert {rule for _, rule in incidents} == {"pitch", "volume"}


def test_fillers_are_scored_per_minute():
    voice = dict.fromkeys(VOICE_RULES, 0)
    body = dict.fromkeys(BODY_RULES, 0)
    # 8 fillers in one minute is at the "bad" rate (8/min): all 15 filler points are lost.
    # Fillers 15 + voice 15 + body 25 = 55 points measured; the score is the percentage kept.
    result = score_presentation(filler_counts={"um": 8, "filler?": 5},
                                voice_incidents=voice, body_incidents=body, duration_seconds=60)
    assert result["max_score"] == 100
    assert result["measured_points"] == 55
    assert result["categories"]["filler"]["deduction"] == 15
    assert result["score"] == 72.7
    assert result["categories"]["voice_tone"]["max_points"] == 15
    assert result["categories"]["body_language"]["max_points"] == 25
    # the same 8 fillers over 10 minutes is under the "good" rate: nothing lost
    relaxed = score_presentation(filler_counts={"um": 8}, voice_incidents=voice, body_incidents=body,
                                 duration_seconds=600)
    assert relaxed["categories"]["filler"]["deduction"] == 0


def test_pace_scores_moderate_delivery_and_consistency():
    steady = score_presentation(pace={"average_wpm": 140, "segment_wpms": [135, 145, 140]})
    assert steady["categories"]["speaking_pace"]["points"] == 10
    assert steady["max_score"] == 100
    assert steady["score"] == 100

    rushed = score_presentation(pace={"average_wpm": 210, "segment_wpms": [100, 190, 240]})
    checks = rushed["categories"]["speaking_pace"]["checks"]
    assert checks["moderate_pace"]["deduction"] == 5
    assert checks["consistent_pace"]["deduction"] > 0


def test_pace_is_not_scored_without_enough_transcript_data():
    assert score_presentation(pace=None)["categories"]["speaking_pace"]["points"] is None


def test_disabled_alerts_exclude_only_their_scoring_checks():
    report = score_presentation(
        filler_counts={"um": 8},
        voice_incidents={"pitch": 4, "quiet": 0},
        body_incidents={"look_down": 5, "tilt": 0},
        pace={"average_wpm": 210, "segment_wpms": [100, 190, 240]},
        script_match=0.1,
        duration_seconds=60,
        disabled_alerts={"filler", "pitch", "look_down", "pace", "out_of_order"},
    )
    checks = report["categories"]
    assert checks["filler"]["points"] is None
    assert set(checks["voice_tone"]["checks"]) == {"quiet"}
    assert set(checks["body_language"]["checks"]) == {"tilt"}
    assert set(checks["speaking_pace"]["checks"]) == {"moderate_pace"}
    assert checks["script_accuracy"]["points"] is None
    assert report["measured_points"] == 12.5                 # quiet 5 + tilt 2.5 + moderate pace 5
    assert report["score"] == 60
    assert [tip["rule"] for tip in report["feedback"]] == ["moderate_pace"]


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


def test_session_score_has_recording_relative_checkpoints():
    session = Session(Mock())
    session.apply_settings({"filler": {"enabled": False}})
    session.audio.set_embedder(lambda texts: np.ones((len(texts), 1), dtype=np.float32))

    class Client:
        sent = []

        async def __aiter__(self):
            yield json.dumps({"type": "script", "text": "Results improved across all markets."})
            yield json.dumps({"type": "session_start", "started_at": 50,
                              "voice_baseline": {"pitch_hz": 180, "volume_dbfs": -20}})
            # One sustained camera incident should give one marker at the onset.
            for i in range(30):
                session.webcam.process({"t": 50.5 + i / 10, "width": 640, "height": 480})

            # Simulate one dropped mic chunk before a filler in the final phrase.
            yield struct.pack("<d", 51) + np.zeros(SR // 20, dtype="<f4").tobytes()
            yield struct.pack("<d", 51.55) + np.zeros(3 * SR, dtype="<f4").tobytes()
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
    assert readings[0]["captured_at"] == pytest.approx(51.6 - 1 / SR)
    assert readings[1]["captured_at"] == pytest.approx(51.7 - 1 / SR)
    assert readings[0]["pitch_times"] == pytest.approx([51.05 - 1 / SR, 51.6 - 1 / SR])
    assert readings[0]["typical_hz"] == 180 and readings[0]["typical_volume_dbfs"] == -20
    assert client.sent[0]["type"] == "script_loaded" and client.sent[0]["ready"]
    transcript, script_position, report = client.sent[-3:]
    assert transcript["fillers"]["you know"] == 1
    assert script_position["type"] == "script_position"
    assert report["type"] == "session_score"
    assert report["categories"]["filler"]["points"] is None
    assert report["checkpoints"] == [
        {"at": 0.5, "category": "body_language", "label": "Show both hands or step back"},
        {"at": 0.5, "category": "body_language", "label": "Stay in frame"},
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
    quiet = 0.02 * np.sin(2 * np.pi * 180 * np.arange(SR * 4) / SR)
    board = AlertBoard()
    a = AudioAnalyzer(stt_model, board)
    a.apply_settings(voice_rules(quiet={"enabled": False}))
    a.reset((180, -17))
    feed_all(a, quiet)
    assert a.voice_incidents["quiet"] == 1
    assert "Speak up" not in board.active()

    board = AlertBoard()
    a = AudioAnalyzer(stt_model, board)
    a.apply_settings(voice_rules(quiet={"duration": 10}))
    a.reset((180, -17))
    feed_all(a, quiet)
    assert a.voice_incidents["quiet"] == 0
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

    # Room hiss below the measured silence level is not treated as quiet speech.
    board = AlertBoard()
    a = AudioAnalyzer(stt_model, board)
    a.set_noise_floor(-50.0)
    a.reset((180, -25))
    msgs = [m for m in feed_all(a, room) if m["type"] == "voice"]
    assert "Speak up" not in board.active()
    assert not msgs[-1]["speaking"] and msgs[-1]["silence_dbfs"] == -45.0

    # ...while speaking above the room level but quietly still counts as speaking quietly
    a = AudioAnalyzer(stt_model, board)
    a.set_noise_floor(-60.0)
    a.reset((180, -25))
    msgs = [m for m in feed_all(a, quiet_voice) if m["type"] == "voice"]
    assert msgs[-1]["speaking"] and msgs[-1]["pitch_hz"]
    assert "Speak up" in board.active()


def test_quiet_voiced_speech_below_room_cutoff_counts_but_noise_does_not():
    board = AlertBoard()
    analyzer = AudioAnalyzer(Mock(), board)
    analyzer.set_noise_floor(-55)
    analyzer.reset((180, -45))
    time = np.arange(SR) / SR
    noise = np.random.default_rng(4).normal(0, 0.0002, SR)
    feed_all(analyzer, noise)
    assert analyzer.voice_incidents.get("quiet", 0) == 0

    quiet_speech = 0.0005 * np.sin(2 * np.pi * 180 * time)
    reading = feed_all(analyzer, quiet_speech)[-1]
    assert reading["volume_dbfs"] < reading["quiet_limit_dbfs"] == -53
    assert reading["volume_dbfs"] < reading["silence_dbfs"] == -65 and reading["speaking"]
    assert "Speak up" in board.active()
    report = score_presentation(voice_incidents=analyzer.voice_incidents)
    assert report["categories"]["voice_tone"]["checks"]["quiet"]["deduction"] == 3.8   # 0.75 of 5


def test_quiet_unpitched_bursts_count_across_short_pauses():
    board = AlertBoard()
    analyzer = AudioAnalyzer(Mock(), board)
    # A bad room measurement would otherwise put the speech cutoff at -42 dBFS.
    analyzer.set_noise_floor(-52)
    analyzer.reset((180, -45))
    rng = np.random.default_rng(18)
    quiet_burst = rng.normal(0, 0.0007, SR // 4)
    pause = np.zeros(SR // 2)
    # Three quarter-second bursts around -63 dBFS, with no reliable pitch.
    readings = feed_all(analyzer, np.concatenate([quiet_burst, pause, quiet_burst, pause, quiet_burst]))
    assert all(msg["pitch_hz"] is None for msg in readings if msg["type"] == "voice")
    assert readings[-1]["silence_dbfs"] == -65
    assert analyzer.voice_incidents["quiet"] == 1
    assert "Speak up" in board.active()
    report = score_presentation(voice_incidents=analyzer.voice_incidents)
    assert report["categories"]["voice_tone"]["checks"]["quiet"]["deduction"] == 3.8   # 0.75 of 5


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


def test_volume_and_quiet_incidents_are_scored_under_voice_tone():
    report = score_presentation(
        voice_incidents={"volume": 4, "quiet": 2, "pitch": 0, "pause": 3},
        volume={"speech_dbfs": -30, "segment_dbfs": [-30, -31, -29], "room_dbfs": -64},
        duration_seconds=60,
    )
    voice = report["categories"]["voice_tone"]
    assert voice["max_points"] == 15
    assert set(voice["checks"]) == {"volume", "quiet", "pitch"}
    assert voice["checks"]["volume"]["deduction"] == 5
    assert voice["checks"]["quiet"]["deduction"] == 5
    assert voice["checks"]["pitch"]["deduction"] == 0


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


def test_going_over_the_time_limit_costs_points():
    def time_check(recorded_s, limit_min):
        report = score_presentation(duration_seconds=recorded_s, expected_minutes=limit_min)
        return report["categories"]["time_limit"]

    assert time_check(9 * 60, 10)["checks"]["over_time"]["deduction"] == 0        # early: fine
    assert time_check(10.4 * 60, 10)["checks"]["over_time"]["deduction"] == 0     # 4% over: grace
    mid = time_check(11.75 * 60, 10)["checks"]["over_time"]                        # 17.5% over
    assert mid["deduction"] == 5 and "18% over your 10:00 limit (11:45)" in mid["feedback"]
    assert time_check(14 * 60, 10)["checks"]["over_time"]["deduction"] == 10       # 40% over: all 10
    assert time_check(14 * 60, None)["max_points"] == 0                            # no limit: not scored
    # without a recorded length the limit is only the fallback length, never "over time"
    assert score_presentation(expected_minutes=10)["categories"]["time_limit"]["max_points"] == 0


def test_skipping_around_the_script_costs_a_point_per_event():
    def order(events, disabled=()):
        report = score_presentation(script_match=1.0, script_order_events=events, disabled_alerts=disabled)
        return report["categories"]["script_accuracy"]["checks"].get("script_order")

    assert order(0)["deduction"] == 0
    assert order(1)["deduction"] == 1 and "1 time." in order(1)["feedback"]
    assert order(9)["deduction"] == 5                                              # capped
    assert order(3, disabled=("out_of_order",)) is None                           # alert off: not scored
    # no script: no script category at all, whatever the count
    assert score_presentation(script_order_events=3)["categories"]["script_accuracy"]["max_points"] == 0


def test_fixed_weights_add_up_to_100_and_scores_are_percentages():
    from presentation_service.scoring import (CATEGORY_WEIGHTS, FILLER_MAX_POINTS, PACE_CHECK_POINTS,
                                              SCRIPT_MAX_POINTS, SCRIPT_OFF_MAX_POINTS,
                                              SCRIPT_ORDER_MAX_POINTS, TIME_MAX_POINTS)
    assert sum(CATEGORY_WEIGHTS.values()) == 100
    assert FILLER_MAX_POINTS == CATEGORY_WEIGHTS["filler"]
    assert sum(r[0] for r in VOICE_RULES.values()) == CATEGORY_WEIGHTS["voice_tone"]
    assert 2 * PACE_CHECK_POINTS == CATEGORY_WEIGHTS["speaking_pace"]
    assert sum(r[0] for r in BODY_RULES.values()) == CATEGORY_WEIGHTS["body_language"]
    assert (SCRIPT_MAX_POINTS + SCRIPT_ORDER_MAX_POINTS + SCRIPT_OFF_MAX_POINTS
            == CATEGORY_WEIGHTS["script_accuracy"])
    assert TIME_MAX_POINTS == CATEGORY_WEIGHTS["time_limit"]

    everything = dict(
        filler_counts={"um": 0}, voice_incidents=dict.fromkeys(VOICE_RULES, 0),
        body_incidents=dict.fromkeys(BODY_RULES, 0),
        pace={"average_wpm": 140, "segment_wpms": [138, 142]},
        script_match=1.0, script_order_events=0, script_off_events=0,
        duration_seconds=540, expected_minutes=10,
    )
    perfect = score_presentation(**everything)
    assert perfect["measured_points"] == 100 and perfect["score"] == 100
    assert all(c["weight"] == CATEGORY_WEIGHTS[n] for n, c in perfect["categories"].items())

    # no script and no time limit: scored out of the other 65 points, still a percentage
    partial = score_presentation(**{**everything, "script_match": None, "expected_minutes": None,
                                    "filler_counts": {"um": 3}})
    assert partial["measured_points"] == 65
    filler = partial["categories"]["filler"]
    assert partial["score"] == round(100 * (65 - filler["deduction"]) / 65, 1)

    # every number has at most 1 decimal
    messy = score_presentation(**{**everything, "voice_incidents": {"volume": 1, "pitch": 0, "quiet": 1},
                                  "body_incidents": {**dict.fromkeys(BODY_RULES, 0), "look_down": 1}})
    numbers = [messy["score"], messy["measured_points"]] + [
        v for c in messy["categories"].values() for v in (c["points"], c["deduction"])
    ] + [ch["deduction"] for c in messy["categories"].values() for ch in c["checks"].values()]
    assert all(round(n, 1) == n for n in numbers if n is not None)


def test_off_script_stretches_cost_a_point_each():
    def off(events):
        report = score_presentation(script_match=1.0, script_order_events=0, script_off_events=events)
        return report["categories"]["script_accuracy"]["checks"]["off_script"]

    assert off(0)["deduction"] == 0
    assert off(2)["deduction"] == 2 and "2 stretches" in off(2)["feedback"]
    assert off(9)["deduction"] == 5                                                # capped

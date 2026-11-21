import shutil
import subprocess
import wave

import numpy as np
import pytest

from presentation_service.audio_analysis import AudioAnalyzer
from presentation_service.server import VOSK_MODEL, AlertBoard
from presentation_service.transcript import load_vosk

SR = 16000


@pytest.fixture(scope="module")
def vosk_model():
    return load_vosk(VOSK_MODEL)


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


def test_tone_gives_pitch_and_volume(vosk_model):
    t = np.arange(SR * 2) / SR
    tone = 0.2 * np.sin(2 * np.pi * 200 * t)
    msgs = [m for m in feed_all(AudioAnalyzer(vosk_model, None, AlertBoard()), tone) if m["type"] == "voice"]
    assert len(msgs) == 20                                      # every 100 ms
    assert abs(msgs[-1]["pitch_hz"] - 200) < 5 and -20 < msgs[-1]["volume_dbfs"] < -10


def test_voice_calibration_from_speechlike_tone(vosk_model):
    a = AudioAnalyzer(vosk_model, None, AlertBoard())
    a.request_calibration()
    t = np.arange(SR * 4) / SR
    msgs = feed_all(a, 0.2 * np.sin(2 * np.pi * 180 * t))
    assert msgs[-1]["calibrated"] and "Voice calibrated: 180 Hz" in msgs[-1]["message"]


def test_quiet_alert_posts_to_board(vosk_model):
    board = AlertBoard()
    feed_all(AudioAnalyzer(vosk_model, None, board), np.zeros(SR * 4, dtype=np.float32))
    assert "Speak up" in board.active()


def test_transcript_and_filler_counts_from_speech(vosk_model, tmp_path):
    speech = say("So I think, you know, the results are like really good. So that is the plan.", tmp_path)
    board = AlertBoard()
    a = AudioAnalyzer(vosk_model, None, board)
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


def test_filler_counts_go_up_before_the_sentence_ends(vosk_model, tmp_path):
    speech = say("I really like this idea and I like the plan", tmp_path)       # no pause: stays partial
    a = AudioAnalyzer(vosk_model, None, AlertBoard())
    msgs = [m for m in feed_all(a, speech) if m["type"] == "transcript"]
    assert not any(m["new_sentences"] for m in msgs)                           # sentence not finished yet
    assert msgs[-1]["fillers"]["like"] == 2 and msgs[-1]["total"] == 2
    final = a.finish()[0]
    assert final["fillers"]["like"] == 2                                       # not double-counted at the end

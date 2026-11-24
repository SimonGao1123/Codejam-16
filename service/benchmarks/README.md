# Transcript and filler benchmarks

How well the app hears fillers, how fast it says so and what it costs; Moonshine against the earlier
Vosk + Whisper pipeline (commit 57319eb), and the app before and after the optimizations. Apple M5, 16 GB, macOS.

**Test set:** 82 min, 457 um / uh: 8 AMI speakers (CC BY 4.0, spontaneous meeting speech, headset mics) and 5
DisfluencySpeech tracks (Apache 2.0, one speaker, every filler use annotated). Settings were chosen on other
recordings (`dev`), the "like" / "so" / "you know" rules on DisfluencySpeech training transcripts.

## Vosk vs Moonshine (test)

| | WER AMI | WER DS | um/uh precision | recall | F1 | alert delay (median) | load | memory |
|---|---|---|---|---|---|---|---|---|
| Vosk small | 26.0% | 13.2% | - | 0% | 0% | none | 0.5 s | 475 MB |
| Vosk large | 15.9% | 10.4% | 100% | 0.4% | 0.9% | none | 7.9 s | 2883 MB |
| Vosk small + Whisper tiny.en | 25.4% | 12.4% | 77.5% | 55.8% | 64.9% | 3.06 s | 2.0 s | 786 MB |
| Vosk large + Whisper tiny.en | 15.7% | 10.1% | 77.8% | 49.9% | 60.8% | 3.11 s | 8.0 s | 3860 MB |
| Moonshine tiny | 17.3% | 9.3% | 90.0% | 51.0% | 65.1% | 0.51 s | 0.2 s | 294 MB |
| Moonshine small | 10.5% | 7.2% | 93.5% | 59.5% | 72.7% | 0.55 s | 0.5 s | 625 MB |
| Moonshine medium (the app before) | 9.2% | 7.0% | 90.6% | 65.2% | 75.8% | 0.70 s | 0.5 s | 1403 MB |
| **the app now** | **9.1%** | **6.9%** | **83.7%** | **83.2%** | **83.4%** | **0.50 s** | 0.2 s | 1169 MB |

Whisper-era pipelines need less CPU (real-time factor 0.15-0.17 against 0.30).

## The app before and after (test unless noted)

| | before | after |
|---|---|---|
| um/uh F1 (95% bootstrap over recordings) | 75.8% (66.2-82.7) | 83.4% (80.3-85.8); difference +1.8 to +16.0 points |
| um/uh recall / precision | 65.2% / 90.6% | 83.2% / 83.7% |
| um/uh count error per recording | 30% (128 of 457 short overall) | 16% (3 short overall) |
| "like" counted as a filler: wrong / real found (DS) | 48 wrong, 12 of 12 | 11 wrong, 9 of 12 |
| "so": wrong / real found | 11 wrong, 36 of 36 | 2 wrong, 36 of 36 |
| "you know": wrong / real found | 2 wrong, 59 of 64 | 0 wrong, 57 of 64 |
| all fillers F1 (DS) | 66.2% | 86.1% |
| alert delay, median / p90 | 0.70 / 3.33 s | 0.50 / 2.58 s |
| fillers alerted live | 83.0% | 90.1% |
| playback marker error, median | 0.37 s | 0.23 s |
| real-time factor (how long an upload takes) | 0.568 | 0.304 (1.9x faster) |
| worst backlog | 7.96 s | 0.97 s |
| 10 min into a session: webcam frames analysed per second | 16.3 of 30 | 30.0 of 30 |
| ... round trip per frame, median / p99 | 46 / 101 ms | 1.2 / 4.2 ms |
| ... time to catch up on 10 min of audio | 486 s | 198 s |
| bundled service, on disk / zipped | 273 / 104 MB | 216 / 79 MB |

The AMI half barely moved on um/uh (F1 83.4% -> 83.8%): more hesitations are found but more are also made up
(some are real ones the AMI transcripts leave out). The gain is on DisfluencySpeech (50.3% -> 82.5%).
Total CPU is about the same (1.5-1.6 s per second of audio); the time saved was on one thread that held Python's GIL.

What each change does:

1. `return_audio_data: false`: every Moonshine update copied the whole session's audio into Python lists, which
   took longer the longer you spoke and stalled the webcam socket. Rest of the pipeline unchanged.
2. Context rules for "like" / "so" / "you know" (`fillers.py`).
3. Hesitations that partials showed but the finished line dropped are put back: recall 65.2 -> 72.2%,
   F1 75.9 -> 80.4% with precision unchanged (90.7%).
4. Key-term biasing towards um / uh at boost 1.5: recall 72.2 -> 83.2%, precision 90.7 -> 83.7%, F1 80.4 -> 83.4%
   (the gain over step 3 alone is borderline: -0.0 to +6.9 points). `KEYTERM_BOOST` is the trade-off.
5. Filler markers placed by word position in the line instead of at its start.
6. Unused dependencies removed (vosk, faster-whisper, av, noisereduce and what it pulls in).

Tried and not used: a longer update interval. At 1 s the app needs a third less CPU (1.11 s against 1.69 per second of
audio, dev) but alerts come 0.35 s later and fewer hesitations are recovered; at 3 s
CPU drops to 0.70, with alerts 1.5 s later.

## Script tracking and webcam data

Second round; "before" is the service after the changes above. 10 min into a session with live audio, a 250-part
script and 30 webcam frames/s (`webcam_stall`, two runs each; "first" = ec6569c, before any optimization, one run):

| | first | before | after |
|---|---|---|---|
| webcam frames analysed per second | 9.3 | 30.0 | 30.0 |
| round trip, median | 3.5 ms | 3.0-3.1 ms | 1.3-1.4 ms |
| round trip, p95 | 313 ms | 6.2-8.2 ms | 3.4-4.1 ms |
| frame sent | 14,370 bytes | 14,370 | 512 |
| reply received (stats off, the default) | 679 bytes | 684 | 28 |
| webcam socket, both ways at 30 frames/s | - | 451 KB/s | 16 KB/s |
| service CPU (cores) | 1.81 | 1.61-1.70 | 1.51-1.66 |

Parsing and analysing a frame: 1.10 -> 0.46 ms in Python; the old and the new analyzer give identical states on 1,800
synthetic frames. Building the message in the app: 0.074 -> 0.004 ms (Node). Recorded landmark logs shrink with the frames
(about 260 -> 9 MB for 10 min, calculated).

Script tracking on the test tracks (`benchmarks.script`: DisfluencySpeech, 250 sentences; tuned on dev):

| | before | after |
|---|---|---|
| position exactly right / within one part | 33.5% / 97.5% | 86.3% / 100% |
| lag behind the speaker, median / p90 | 5.9 / 8.1 s | 1.5 / 2.3 s |
| false alerts while reading the script (33 min) | 6 | 0 |
| skipped single sentences found / wrongly reported | 20 of 20 / 6 | 20 of 20 / 0 |
| skipped groups of 3: sentences found / wrongly reported | 60 of 60 / 6 | 59 of 60 / 0 |
| going back noticed | 10 of 10 | 10 of 10 |
| reading someone else's script: updates flagged off script | 91.5% | 91.3% |
| one update, 50 / 250 parts | 6.9 / 11.3 ms | 5.8 / 6.6 ms |
| catching up on 1,500 words, 250 parts | 1656 ms | 681 ms |
| preparing a script, 50 / 250 parts | 134 / 706 ms | 386 / 1835 ms (slower) |
| ... after an edit | 134 / 706 ms | 26 / 42 ms |

How: the script is also embedded as overlapping 12-word windows and the latest words are compared with all of them at
once, which says where the speaker is; whether they're on script at all is still decided by the sentences. Scoring is
matrix products, catching up is embedded in one batch, and checks run every 0.5 s instead of 1.5 s.
Webcam: only the landmarks used are sent, replies only have what changed and is shown, WebSocket compression is off, the
gesture-repetition check is vectorized.

## Limits

One machine, one speaker for the "like" / "so" / "you know" numbers (12 real "like", so those percentages are
rough), all speakers in headset or studio audio. AMI meetings aren't presentations.

## Run it

```sh
uv run --group bench python -m benchmarks.prepare_data      # downloads the recordings (untracked)
uv run --group bench python -m benchmarks.run app           # or moonshine-medium, vosk-small+whisper, ...
uv run --group bench python -m benchmarks.score
uv run --group bench python -m benchmarks.script            # script tracking, on the transcripts of a saved "app" run
uv run --group bench python -m benchmarks.webcam_stall      # 10 min of session, then webcam + audio + script together
```

`vosk-large` needs `models/vosk-model-en-us-0.22`. One run per engine takes about 15 min (Moonshine medium 25-50).

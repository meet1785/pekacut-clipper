"""Multi-format subtitle export (SRT and WebVTT) and caption downloader tests."""
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-subs-")
os.environ.update(DATABASE_URL="", PEAKCUT_ACCOUNTS="0", PEAKCUT_AI_PROVIDER="gemini",
                  PEAKCUT_AI_MODELS="", PEAKCUT_AI_KEY="", PEAKCUT_AI_KEY_FILE="",
                  PEAKCUT_AI_BASE_URL="", PEAKCUT_PUBLIC_URL="http://127.0.0.1:8765")
sys.path.insert(0, ".")

from fastapi.testclient import TestClient  # noqa: E402
from studio import account, config, ctx, db, jobs, render, server  # noqa: E402

db.init()
fails = []
BASE = "http://127.0.0.1:8765"
c = TestClient(server.app, base_url=BASE, follow_redirects=False)


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


# ---- 1. SubRip (.srt) and WebVTT (.vtt) timestamp formatting
check("SRT time zero", render._srt_time(0.0), "00:00:00,000")
check("SRT time sub-second", render._srt_time(1.456), "00:00:01,456")
check("SRT time minute rollover", render._srt_time(65.12), "00:01:05,120")
check("SRT time hour rollover", render._srt_time(3661.05), "01:01:01,050")

check("VTT time zero", render._vtt_time(0.0), "00:00:00.000")
check("VTT time sub-second", render._vtt_time(1.456), "00:00:01.456")
check("VTT time minute rollover", render._vtt_time(65.12), "00:01:05.120")
check("VTT time hour rollover", render._vtt_time(3661.05), "01:01:01.050")

# ---- 2. Empty transcripts produce valid, clean output
check("SRT handles empty words", render.build_srt([], 0.0, 30.0), "")
check("WebVTT handles empty words", render.build_vtt([], 0.0, 30.0), "WEBVTT\n")

# ---- 3. Generating subtitles relative to clip timeline
sample_words = [
    {"w": "Intro", "s": 5.0, "e": 5.8},           # outside clip range (< 10.0)
    {"w": "Welcome", "s": 10.2, "e": 10.6},       # relative: 0.2s
    {"w": "to", "s": 10.7, "e": 10.9},            # relative: 0.7s
    {"w": "PeakCut", "s": 11.0, "e": 11.5},       # relative: 1.0s
    {"w": "auto", "s": 11.6, "e": 12.0},          # relative: 1.6s
    {"w": "clipper.", "s": 12.1, "e": 12.5},      # relative: 2.1s
    {"w": "Outro", "s": 35.0, "e": 36.0},         # outside clip range (> 20.0)
]

clip_start, clip_end = 10.0, 20.0
srt = render.build_srt(sample_words, clip_start, clip_end)
vtt = render.build_vtt(sample_words, clip_start, clip_end)

check("SRT contains relative cue number", srt.startswith("1\n"), True)
check("SRT timestamps are relative to clip start", "00:00:00,200 --> " in srt, True)
check("SRT excludes words before start", "Intro" not in srt, True)
check("SRT excludes words after end", "Outro" not in srt, True)
check("SRT contains formatted caption text", "Welcome to PeakCut auto clipper." in srt, True)

check("WebVTT starts with WEBVTT header", vtt.startswith("WEBVTT\n"), True)
check("WebVTT uses dot decimal separator", "00:00:00.200 --> " in vtt, True)
check("WebVTT excludes words before start", "Intro" not in vtt, True)
check("WebVTT excludes words after end", "Outro" not in vtt, True)

# ---- 4. HTTP API Subtitle Download Endpoints
# Create a test job in user's job directory with transcript words.json and a clip
u_dir = config.user_dir("u_local")
jid = "20261115-test-subtitles"
jdir = u_dir / "jobs" / jid
jdir.mkdir(parents=True, exist_ok=True)
cdir = jdir / "clips"
cdir.mkdir(parents=True, exist_ok=True)

test_words = [
    {"w": "Hello", "s": 1.0, "e": 1.5},
    {"w": "world,", "s": 1.6, "e": 2.0},
    {"w": "subtitles", "s": 2.1, "e": 2.8},
    {"w": "work!", "s": 2.9, "e": 3.5},
]
(jdir / "words.json").write_text(json.dumps(test_words), encoding="utf-8")

job_data = {
    "id": jid,
    "user": "u_local",
    "status": "done",
    "kind": "video",
    "title": "Subtitle Test Video",
    "dir": str(jdir),
    "clips": [
        {
            "id": "clip01",
            "start": 0.0,
            "end": 10.0,
            "status": "done",
            "file": "clip01.mp4",
            "title": "Subtitle Test Clip",
        }
    ]
}
jobs.save(job_data)
jobs._jobs[jid] = job_data
jobs._by_user.setdefault("u_local", {})[jid] = job_data

# Test requesting SRT subtitles
r_srt = c.get(f"/api/jobs/{jid}/clips/clip01/subtitles?format=srt")
check("SRT endpoint returns status 200", r_srt.status_code, 200)
check("SRT endpoint sets subrip Content-Type", "application/x-subrip" in r_srt.headers.get("content-type", ""), True)
check("SRT Content-Disposition attachment", 'attachment; filename="clip01.srt"' in r_srt.headers.get("content-disposition", ""), True)
check("SRT body contains expected caption words", "Hello world, subtitles work!" in r_srt.text, True)
check("SRT file saved on disk for caching", (cdir / "clip01.srt").is_file(), True)

# Test requesting WebVTT subtitles
r_vtt = c.get(f"/api/jobs/{jid}/clips/clip01/subtitles?format=vtt")
check("WebVTT endpoint returns status 200", r_vtt.status_code, 200)
check("WebVTT endpoint sets text/vtt Content-Type", "text/vtt" in r_vtt.headers.get("content-type", ""), True)
check("WebVTT Content-Disposition attachment", 'attachment; filename="clip01.vtt"' in r_vtt.headers.get("content-disposition", ""), True)
check("WebVTT body starts with WEBVTT", r_vtt.text.startswith("WEBVTT"), True)
check("WebVTT file saved on disk for caching", (cdir / "clip01.vtt").is_file(), True)

# Test invalid subtitle format
r_bad = c.get(f"/api/jobs/{jid}/clips/clip01/subtitles?format=invalid_fmt")
check("Invalid format returns 400", r_bad.status_code, 400)

# Test non-existent clip
r_404 = c.get(f"/api/jobs/{jid}/clips/clip_missing/subtitles?format=srt")
check("Missing clip returns 404", r_404.status_code, 404)

# Test /media endpoint directly serves .srt and .vtt
r_med_srt = c.get(f"/media/{jid}/clip01.srt")
check("/media serves .srt", r_med_srt.status_code, 200)
r_med_vtt = c.get(f"/media/{jid}/clip01.vtt")
check("/media serves .vtt", r_med_vtt.status_code, 200)

# ---- 5. Bulk Zip Export Includes Matching Subtitle Files
# Create dummy mp4 file so zip_clips finds the clip
(cdir / "clip01.mp4").write_bytes(b"dummy video data")

with ctx.as_user("u_local"):
    zip_path = account.zip_clips([(jid, "clip01")])
    check("zip_clips created a file", zip_path.is_file(), True)
    with zipfile.ZipFile(zip_path, "r") as z:
        names = z.namelist()
        check("ZIP includes .mp4", any(n.endswith(".mp4") for n in names), True)
        check("ZIP includes matching .srt subtitle file", any(n.endswith(".srt") for n in names), True)
        check("ZIP includes matching .vtt subtitle file", any(n.endswith(".vtt") for n in names), True)
    zip_path.unlink(missing_ok=True)

if fails:
    print(f"\n{len(fails)} checks failed.")
    sys.exit(1)
else:
    print(f"\nAll subtitle tests passed.")
    sys.exit(0)

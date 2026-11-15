"""Tests for intelligent, coherent clip selection and boundary snapping."""
import os
import sys
import tempfile

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-cliptest-")
os.environ.update(DATABASE_URL="", PEAKCUT_ACCOUNTS="0", PEAKCUT_AI_PROVIDER="gemini",
                  PEAKCUT_AI_MODELS="", PEAKCUT_AI_KEY="", PEAKCUT_AI_KEY_FILE="",
                  PEAKCUT_AI_BASE_URL="", PEAKCUT_PUBLIC_URL="http://127.0.0.1:8765")
sys.path.insert(0, ".")

from studio import brain

fails = []

def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


def make_transcript(sentences, gap=0.3):
    t = 0.0
    words = []
    for s in sentences:
        tokens = s.split()
        for i, tok in enumerate(tokens):
            words.append({"w": tok, "s": round(t, 2), "e": round(t + 0.35, 2)})
            t += 0.4
        t += gap
    return words


# 1. Test sentence_starts detection
sents = [
    "Welcome to the challenge today.",
    "One 1v1, you two meet in the field.",
    "We'll stay right here.",
    "What is going to happen next?",
]
words = make_transcript(sents, gap=0.8)
starts = brain.sentence_starts(words)

check("sentence_starts finds all sentence beginnings", len(starts), 4)
check("first sentence starts at 0.0", starts[0], 0.0)

# Verify word text at each detected start
start_words = [next(w["w"] for w in words if abs(w["s"] - st) < 0.01) for st in starts]
check("starts match true sentence openers", start_words, ["Welcome", "One", "We'll", "What"])

# 2. Test snap start when AI picks mid-sentence
lines = brain.make_lines(words)
# The second sentence is "One 1v1, you two meet in the field." (from ~2.3s to ~4.7s)
# Suppose the AI picked start at 3.5s ("meet in the field")
raw_clip = {"start": 3.5, "end": 6.5}
snapped_s, snapped_e = brain._snap(raw_clip, lines, words, min_len=4, max_len=15, duration=30.0)

clip_words = [w["w"] for w in words if snapped_s <= w["s"] <= snapped_e]
check("snap moves mid-sentence start to beginning of sentence", clip_words[0], "One")
check("snap does not start mid-sentence", clip_words[0] != "meet", True)

# 3. Test clean boundary margins (no audio bleeding)
# Lead margin should not bleed into previous sentence
second_sent_start = starts[1]
prev_word = next(w for w in words if w["w"] == "today.")
check("lead margin stays after previous word end", snapped_s >= prev_word["e"], True)

# Tail margin should not bleed into next sentence start
last_word_in_clip = next(w for w in words if w["e"] <= snapped_e)
next_word_after_clip = next(w for w in words if w["s"] > snapped_e)
check("tail margin ends cleanly before next sentence starts", snapped_e < next_word_after_clip["s"], True)
check("tail margin leaves safe silence gap before next speaker", (next_word_after_clip["s"] - snapped_e) >= 0.03, True)

# 4. Test speaker change turn markers (">>")
dialogue = [
    ">> I won the prize!",
    ">> No way that actually happened.",
]
d_words = make_transcript(dialogue, gap=0.5)
d_starts = brain.sentence_starts(d_words)
check("speaker turn markers detected as sentence starts", len(d_starts), 2)

# 5. Test empty transcript edge cases
check("empty words produces empty starts", brain.sentence_starts([]), [])
check("invalid clip returns zero span", brain._snap({"start": -1, "end": -1}, [], [], 5, 30, 60), (0.0, 0.0))

print("\nALL PASSED" if not fails else f"\n{len(fails)} checks failed.")
import shutil
shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)

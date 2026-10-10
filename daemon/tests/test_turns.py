"""Turn assembly, pick binding and continuation rules (SPEC §6.9), pure."""

from __future__ import annotations

from sidecall.voice.turns import (
    CONTINUATION_GAP_MS,
    LATE_PICK_MS,
    PICK_HOLD_MS,
    PRE_SPEECH_MS,
    PickTime,
    Segment,
    SubmittedTurn,
    TimedWord,
    TurnTracker,
    assemble_turn,
    bind_picks,
    can_continue,
    monotonic_to_daemon_ms,
    pick_hold_until,
    place_markers,
    spoken_words,
    turn_window,
)

T0 = 1_700_000_000_000.0  # daemon ms


def words(spec: str, start: float = T0, step: float = 400.0) -> tuple[TimedWord, ...]:
    """Words 300 ms long, one every `step` ms from `start`."""
    out: list[TimedWord] = []
    for i, w in enumerate(spec.split()):
        s = start + i * step
        out.append(TimedWord(w, s, s + 300))
    return tuple(out)


def seg(spec: str, start: float = T0, step: float = 400.0) -> Segment:
    return Segment(spec, words(spec, start, step), start)


def test_markers_mid_sentence_after_word_end() -> None:
    # make@0-300 this@400-700 match@800-1100 that@1200-1500
    ws = words("make this match that.")
    picks = [PickTime(1, T0 + 750), PickTime(2, T0 + 1600)]
    assert place_markers(ws, picks) == "make this [1] match that [2]."


def test_marker_uses_word_end_not_start() -> None:
    ws = words("make this match that")
    # 650 ms: "this" started (400) but hasn't ended (700) → after "make".
    assert place_markers(ws, [PickTime(1, T0 + 650)]) == "make [1] this match that"
    # Exactly at the end counts as ended.
    assert place_markers(ws, [PickTime(1, T0 + 700)]) == "make this [1] match that"


def test_picks_before_speech_are_prefixed_in_order() -> None:
    ws = words("put a border around it")
    picks = [PickTime(4, T0 - 500), PickTime(3, T0 - 1500)]
    assert place_markers(ws, picks) == "[3] [4] put a border around it"


def test_pick_during_first_word_is_prefixed() -> None:
    # The first word's start can be ~0.4 s early; only its end counts.
    ws = words("this one")
    assert place_markers(ws, [PickTime(1, T0 + 100)]) == "[1] this one"


def test_multiple_picks_after_same_word() -> None:
    ws = words("swap these, please")
    picks = [PickTime(2, T0 + 900), PickTime(1, T0 + 750)]
    assert place_markers(ws, picks) == "swap these [1] [2], please"


def test_window_binds_only_picks_in_range() -> None:
    segments = [seg("make this red")]
    end = T0 + 2000
    lo, hi = turn_window(segments, end)
    assert (lo, hi) == (T0 - PRE_SPEECH_MS, end)
    picks = [
        PickTime(1, T0 - PRE_SPEECH_MS - 1),  # too early
        PickTime(2, T0 - 1000),  # before speech, inside the window
        PickTime(3, T0 + 500),
        PickTime(4, end + 1),  # after the turn
    ]
    assert [p.id for p in bind_picks(picks, (lo, hi))] == [2, 3]
    turn = assemble_turn(segments, picks, end)
    assert turn.text == "[2] make [3] this red"
    assert turn.pick_ids == (2, 3)


def test_segment_start_earlier_than_first_word_widens_window() -> None:
    s = Segment("hi", words("hi", T0), start=T0 - 200)
    assert turn_window([s], T0 + 1000)[0] == T0 - 200 - PRE_SPEECH_MS


def test_multi_segment_turn_concatenates_words() -> None:
    first = seg("make this", T0)
    second = seg("match that one.", T0 + 2000)
    picks = [PickTime(1, T0 + 1200), PickTime(2, T0 + 3200)]
    turn = assemble_turn([first, second], picks, T0 + 3500)
    assert turn.text == "make this [1] match that one [2]."
    assert turn.pick_ids == (1, 2)


def test_no_picks_plain_text() -> None:
    turn = assemble_turn([seg("hello there")], [], T0 + 1000)
    assert turn.text == "hello there"
    assert turn.pick_ids == ()


def test_segment_without_word_times_appends_markers() -> None:
    turn = assemble_turn([Segment("make it red")], [PickTime(5, T0 - 10)], T0)
    assert turn.text == "make it red [5]"
    assert turn.pick_ids == (5,)


def test_bare_punctuation_word() -> None:
    ws = (TimedWord("ok", T0, T0 + 100), TimedWord("...", T0 + 150, T0 + 200))
    assert place_markers(ws, [PickTime(1, T0 + 300)]) == "ok ... [1]"


def test_monotonic_conversion() -> None:
    assert monotonic_to_daemon_ms(10.0, offset_s=5.0) == 15000.0
    assert abs(monotonic_to_daemon_ms(0.0) - (monotonic_to_daemon_ms(1.0) - 1000)) < 5


# --- continuation --------------------------------------------------------------------


def test_spoken_words_estimate() -> None:
    assert spoken_words(None, T0) == 0
    assert spoken_words(T0, T0 - 5) == 0
    assert 2.0 < spoken_words(T0, T0 + 1000) < 3.0


def test_can_continue_gap_and_heard_words() -> None:
    end = T0
    assert can_continue(end, end + 500, None)
    assert can_continue(end, end + CONTINUATION_GAP_MS, None)
    assert not can_continue(end, end + CONTINUATION_GAP_MS + 1, None)
    # Audio started 0.5 s before the resume: a word or two heard → still continues.
    assert can_continue(end, end + 1000, end + 500)
    # Two seconds of reply heard: a new turn.
    assert not can_continue(end, end + 1400, end - 600)


def test_pick_hold() -> None:
    now = T0
    assert pick_hold_until(now, []) is None
    assert pick_hold_until(now, [PickTime(1, now - PICK_HOLD_MS - 1)]) is None
    assert pick_hold_until(now, [PickTime(1, now - 300), PickTime(2, now - 5000)]) == (
        now - 300 + PICK_HOLD_MS
    )
    assert pick_hold_until(now, [PickTime(1, now - PICK_HOLD_MS)]) is None


def _submitted(tracker: TurnTracker, segments: list[Segment], end: float) -> SubmittedTurn:
    turn = assemble_turn(segments, [], end)
    sub = SubmittedTurn("u1", segments, end, turn.window, turn.pick_ids, turn.text)
    tracker.submitted(sub)
    return sub


def test_tracker_merges_quick_resume() -> None:
    tr = TurnTracker()
    tr.speech_started(T0, None)
    tr.add_segment(seg("change the submit"))
    tr.speech_stopped()
    segments, merged = tr.take()
    assert merged is None
    last = _submitted(tr, segments, T0 + 1500)

    tr.speech_started(T0 + 2000, None)  # 0.5 s later, no audio yet
    tr.add_segment(seg("button label", T0 + 2000))
    tr.speech_stopped()
    segments, merged = tr.take()
    assert merged is last
    assert [s.text for s in segments] == ["change the submit", "button label"]


def test_tracker_new_turn_after_long_gap_or_heard_reply() -> None:
    tr = TurnTracker()
    tr.add_segment(seg("first"))
    segments, _ = tr.take()
    _submitted(tr, segments, T0)
    tr.speech_started(T0 + 3000, None)
    tr.add_segment(seg("second", T0 + 3000))
    assert tr.take()[1] is None

    tr.speech_stopped()
    _submitted(tr, [seg("second", T0 + 3000)], T0 + 4000)
    tr.speech_started(T0 + 4500, audio_started_at=T0 + 3000)  # 1.5 s of reply heard
    tr.add_segment(seg("third", T0 + 4500))
    assert tr.take()[1] is None


def test_tracker_decides_once_per_speech_start() -> None:
    tr = TurnTracker()
    _submitted(tr, [seg("first")], T0)
    tr.speech_started(T0 + 200, None)
    tr.speech_started(T0 + 5000, None)  # duplicate report of the same start: ignored
    tr.add_segment(seg("more", T0 + 200))
    assert tr.take()[1] is not None


def test_tracker_put_back_keeps_held_turn_open() -> None:
    tr = TurnTracker()
    tr.add_segment(seg("make this"))
    held, merged = tr.take()
    assert merged is None
    tr.put_back(held)
    tr.speech_started(T0 + 1500, None)
    tr.add_segment(seg("bigger", T0 + 1500))
    segments, merged = tr.take()
    assert merged is None
    assert [s.text for s in segments] == ["make this", "bigger"]


def test_tracker_put_back_of_a_continuation_still_merges() -> None:
    tr = TurnTracker()
    last = _submitted(tr, [seg("first")], T0)
    tr.put_back([seg("second", T0 + 500)], continuation=True)
    tr.speech_started(T0 + 900, None)
    tr.add_segment(seg("third", T0 + 900))
    segments, merged = tr.take()
    assert merged is last
    assert [s.text for s in segments] == ["first", "second", "third"]


def test_late_pick_fits() -> None:
    tr = TurnTracker()
    last = _submitted(tr, [seg("make this red")], T0 + 1500)
    lo, hi = last.window
    assert tr.late_pick_fits(PickTime(7, T0 + 500), T0 + 1800, None)
    assert tr.late_pick_fits(PickTime(7, hi + LATE_PICK_MS), T0 + 2600, None)
    assert not tr.late_pick_fits(PickTime(7, hi + LATE_PICK_MS + 1), T0 + 2600, None)
    assert not tr.late_pick_fits(PickTime(7, lo - 1), T0 + 1800, None)
    # The user has heard two seconds of the reply: too late to redo it.
    assert not tr.late_pick_fits(PickTime(7, T0 + 500), T0 + 4000, T0 + 2000)
    # The user is already talking again.
    tr.speech_started(T0 + 1900, None)
    assert not tr.late_pick_fits(PickTime(7, T0 + 500), T0 + 1950, None)


def test_parakeet_punctuation_does_not_extend_word_end() -> None:
    from types import SimpleNamespace

    from sidecall.voice.services import tokens_to_words

    def tok(text: str, start: float, end: float) -> SimpleNamespace:
        return SimpleNamespace(text=text, start=start, end=end, confidence=0.9)

    tokens = [
        tok(" M", 0.32, 0.56),
        tok("ake", 0.56, 0.88),
        tok(" this", 0.88, 1.2),
        tok(".", 1.2, 1.44),
        tok(" it's", 1.5, 1.7),
    ]
    ws = tokens_to_words(tokens, offset=10.0)
    assert [(w.word, w.start, w.end) for w in ws] == [
        ("Make", 10.32, 10.88),
        ("this.", 10.88, 11.2),
        ("it's", 11.5, 11.7),
    ]


def test_trim_trailing_silence_keeps_speech_plus_pad() -> None:
    import numpy as np

    from sidecall.voice.services import TRAILING_KEEP_SECS, trim_trailing_silence

    sr = 16000
    speech = (0.3 * np.sin(np.linspace(0, 800, sr))).astype(np.float32)  # 1 s
    audio = np.concatenate([np.zeros(sr // 2, np.float32), speech, np.zeros(sr, np.float32)])
    out = trim_trailing_silence(audio, sr)
    assert abs(len(out) / sr - (1.5 + TRAILING_KEEP_SECS)) < 0.03
    assert len(trim_trailing_silence(np.zeros(sr, np.float32), sr)) == sr
    assert len(trim_trailing_silence(np.zeros(5, np.float32), sr)) == 5


def test_first_clause_ready() -> None:
    from sidecall.voice.talker import first_clause_ready

    assert not first_clause_ready("Sure,")
    assert first_clause_ready("I'm not sure what you'd like to change—")
    assert first_clause_ready("The card header uses sixteen pixels of padding, ")
    assert not first_clause_ready("The card header uses sixteen pixels of padding and")

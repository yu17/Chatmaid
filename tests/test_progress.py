import io
import logging
import os
import unicodedata

from chatmaid.init.progress import Progress, ProgressLogHandler


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_progress_tty_redraws_in_place():
    out = Tty()
    t = [0.0]
    p = Progress("digest", 4, stream=out, clock=lambda: t[0])
    p.update(0, "1 first")
    t[0] = 3600
    p.update(2, "3 third")
    p.finish("done")
    text = out.getvalue()
    assert text.count("\r") == 2 and text.count("\n") == 2
    assert "digest 2/4  50% [##########----------] 2/h eta 1h00m elapsed 1h00m | 3 third" in text


def test_progress_non_tty_prints_every_50_and_finish():
    out = io.StringIO()
    p = Progress("digest", 120, stream=out, every=50)
    for i in range(1, 121):
        p.update(i)
    p.finish("summary")
    lines = out.getvalue().splitlines()
    assert [l.split()[1] for l in lines[:3]] == ["50/120", "100/120", "120/120"]
    assert lines[3] == "summary" and len(lines) == 4
    assert "\r" not in out.getvalue()


def test_progress_label_is_cut_to_40_chars():
    out = Tty()
    p = Progress("x", 1, stream=out)
    p.update(0, "a" * 60)
    assert "a" * 41 not in out.getvalue()


def _logger(stream):
    log = logging.getLogger("test_progress.redraw")
    log.handlers[:] = [ProgressLogHandler(stream)]
    log.propagate = False
    return log


def test_log_record_clears_and_redraws_the_line():
    out = Tty()
    p = Progress("digest", 4, stream=out)
    p.update(1, "x")
    first = out.getvalue()
    _logger(out).warning("digest failed for conversation 7")
    after = out.getvalue()[len(first):]
    line = first.removeprefix("\r")
    cleared = after.index("\r")
    record = after.index("digest failed for conversation 7\n")
    redraw = after.index("\r" + line, record)
    assert cleared < record < redraw
    assert after.endswith("\r" + line)
    p.finish()
    assert Progress.active is None

    plain = io.StringIO()
    q = Progress("digest", 4, stream=plain)
    q.update(1, "x")
    _logger(plain).warning("plain record")
    q.finish()
    assert "plain record\n" in plain.getvalue() and "\r" not in plain.getvalue()


def _columns(text):
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


class SizedTty(Tty):
    def fileno(self):
        return 99


def test_progress_cuts_to_terminal_width(monkeypatch):
    monkeypatch.setattr(os, "get_terminal_size", lambda fd: os.terminal_size((60, 20)))
    out = SizedTty()
    p = Progress("digest", 4, stream=out)
    p.update(1, "中文标题" * 10)
    line = out.getvalue().removeprefix("\r")
    assert line.startswith("digest 1/4") and _columns(line) <= 59
    p.finish()


def test_progress_finish_is_idempotent_and_clears_active():
    out = Tty()
    p = Progress("digest", 4, stream=out)
    p.update(1, "x")
    assert Progress.active is p
    p.finish("summary")
    p.finish("summary")
    assert Progress.active is None
    assert out.getvalue().count("summary\n") == 1 and out.getvalue().endswith("summary\n")

import io

from chatmaid.init.progress import Progress


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

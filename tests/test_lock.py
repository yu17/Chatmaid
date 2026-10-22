import pytest

from chatmaid.lock import Busy, exclusive


def test_exclusive_blocks_second_holder(tmp_path):
    p = tmp_path / "x.lock"
    with exclusive(p):
        with pytest.raises(Busy):
            with exclusive(p):
                pass
    with exclusive(p):  # released after exit
        pass

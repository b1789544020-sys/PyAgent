"""DR_0025：简单 open/read/close 块改为 with，异常传播且关闭一次。"""

import io
from unittest.mock import patch


class RecordingFile:
    def __init__(self, fail=False):
        self._file = io.StringIO("payload\n")
        self.fail = fail
        self.close_calls = 0

    @property
    def closed(self):
        return self._file.closed

    def readline(self):
        if self.fail:
            raise OSError("injected read failure")
        return self._file.readline()

    def close(self):
        self.close_calls += 1
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


BAD_SOURCE = 'file = open("draft.txt")\nline = file.readline()\nfile.close()\n'
FIXED_SOURCE = 'with open("draft.txt") as file:\n    line = file.readline()\n'


def run_source(source, recording_file):
    namespace = {}
    with patch("builtins.open", return_value=recording_file):
        exec(source, namespace)
    return namespace


def test_DR_0025():
    bad_normal = RecordingFile()
    bad_result = run_source(BAD_SOURCE, bad_normal)
    assert bad_result["line"] == "payload\n"
    assert bad_normal.close_calls == 1

    bad_failing = RecordingFile(fail=True)
    try:
        run_source(BAD_SOURCE, bad_failing)
    except OSError as exc:
        assert "injected read failure" in str(exc)
    else:
        raise AssertionError("反例必须保留注入的读取异常")
    assert bad_failing.close_calls == 0, "反例应复现异常路径跳过 close"

    fixed_normal = RecordingFile()
    fixed_result = run_source(FIXED_SOURCE, fixed_normal)
    assert fixed_result["line"] == bad_result["line"]
    assert fixed_normal.close_calls == 1

    fixed_failing = RecordingFile(fail=True)
    try:
        run_source(FIXED_SOURCE, fixed_failing)
    except OSError as exc:
        assert "injected read failure" in str(exc)
    else:
        raise AssertionError("with 不得吞掉原读取异常")
    assert fixed_failing.closed
    assert fixed_failing.close_calls == 1


if __name__ == "__main__":
    test_DR_0025()
    print("DR_0025: PASS")

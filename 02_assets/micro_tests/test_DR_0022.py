"""DR_0022：单文件局部结构只把裸 except 缩窄为 FileNotFoundError。"""

import tempfile
from pathlib import Path
from unittest.mock import patch


BAD_SOURCE = '''def read_file(path):
    try:
        file = open(path, "r", encoding="utf-8")
        content = file.read()
        file.close()
        return content
    except:
        return "文件不存在"
'''


class BrokenFile:
    def read(self):
        raise NameError("injected read bug")

    def close(self):
        pass


def test_DR_0022():
    fixed_source = BAD_SOURCE.replace("    except:\n", "    except FileNotFoundError:\n", 1)
    assert fixed_source != BAD_SOURCE
    bad_namespace = {}
    fixed_namespace = {}
    exec(BAD_SOURCE, bad_namespace)
    exec(fixed_source, fixed_namespace)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        missing = root / "missing.txt"
        existing = root / "existing.txt"
        existing.write_text("正文", encoding="utf-8")

        assert bad_namespace["read_file"](str(missing)) == "文件不存在"
        assert fixed_namespace["read_file"](str(missing)) == "文件不存在"
        assert bad_namespace["read_file"](str(existing)) == "正文"
        assert fixed_namespace["read_file"](str(existing)) == "正文"

    with patch("builtins.open", return_value=BrokenFile()):
        assert bad_namespace["read_file"]("unused") == "文件不存在", (
            "反例应复现裸 except 把 NameError 错报为文件不存在"
        )

    with patch("builtins.open", return_value=BrokenFile()):
        try:
            fixed_namespace["read_file"]("unused")
        except NameError as exc:
            assert "injected read bug" in str(exc)
        else:
            raise AssertionError("修复后非 FileNotFoundError 必须继续向外传播")


if __name__ == "__main__":
    test_DR_0022()
    print("DR_0022: PASS")

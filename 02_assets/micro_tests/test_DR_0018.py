"""DR_0018：完整字节已验证为 UTF-8 时显式指定 encoding='utf-8'。"""

import builtins
import tempfile
from pathlib import Path
from unittest.mock import patch


def test_DR_0018():
    original_text = "中文🙂"
    original_bytes = original_text.encode("utf-8")
    real_open = builtins.open

    def open_with_gbk_default(file, mode="r", *args, **kwargs):
        if "b" not in mode and kwargs.get("encoding") is None:
            kwargs["encoding"] = "gbk"
        return real_open(file, mode, *args, **kwargs)

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "utf8.txt"
        path.write_bytes(original_bytes)
        assert original_bytes.decode("utf-8") == original_text

        def run_source(source):
            namespace = {"path": str(path)}
            with patch("builtins.open", new=open_with_gbk_default):
                exec(source, namespace)
            return namespace["text"]

        bad_source = "with open(path, 'r') as file:\n    text = file.read()\n"
        try:
            run_source(bad_source)
        except UnicodeDecodeError:
            pass
        else:
            raise AssertionError("反例没有复现非 UTF-8 默认编码导致的解码错误")

        fixed_source = (
            "with open(path, 'r', encoding='utf-8') as file:\n"
            "    text = file.read()\n"
        )
        assert run_source(fixed_source) == original_text
        assert path.read_bytes() == original_bytes, "修复不得转码或覆盖原文件字节"


if __name__ == "__main__":
    test_DR_0018()
    print("DR_0018: PASS")

"""DR_0019：本地假响应的字节验证为 UTF-8 后设置 response.encoding。"""

import json

import requests


def make_response(content):
    response = requests.Response()
    response.status_code = 200
    response._content = content
    response.encoding = "gbk"
    response.headers["Content-Type"] = "application/json; charset=gbk"
    return response


def test_DR_0019():
    payload = {"消息": "你好"}
    expected_text = json.dumps(payload, ensure_ascii=False)
    content = expected_text.encode("utf-8")

    broken_response = make_response(content)
    assert broken_response.text != expected_text, "反例应复现错误 encoding 导致的乱码"

    fixed_response = make_response(content)
    assert fixed_response.content.decode("utf-8") == expected_text
    fixed_response.encoding = "utf-8"
    assert fixed_response.text == expected_text
    assert json.loads(fixed_response.text) == payload
    assert fixed_response.content == content, "修复不得修改响应正文"


if __name__ == "__main__":
    test_DR_0019()
    print("DR_0019: PASS")

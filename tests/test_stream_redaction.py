"""任意分片凭据不得先进入 SSE；普通文字不等待整条消息。"""

from melonclaw.output.stream_redaction import StreamingRedactor


def test_credentials_are_redacted_at_every_split():
    for secret in ['api_key="offline-private-value"', "OPENAI_API_KEY=offline-private-value", "sk-offlineprivatevalue",
        '"api_key"  :  "offline-private-value"', "apikey = offline-private-value", "Authorization: Bearer offline-private-value"]:
        raw = "开始 " + secret + "\n结束"
        for split in range(1, len(raw)):
            redactor = StreamingRedactor()
            chunks = [redactor.feed(raw[:split]), redactor.feed(raw[split:]), redactor.feed("", final=True)]
            assert "offline-private-value" not in "".join(chunks)
            assert "offlineprivatevalue" not in "".join(chunks)
            assert "开始" in "".join(chunks) and "结束" in "".join(chunks)


def test_plain_content_is_immediate_and_long_secret_buffer_is_bounded():
    redactor = StreamingRedactor()
    assert redactor.feed("我先读取资料。") == "我先读取资料。"
    assert redactor.feed('api_key="' + "x" * 5000).endswith("<redacted>")
    assert len(redactor.pending) == 0
    assert redactor.feed('more-secret" 正文') == '" 正文'
    assert redactor.feed("sk-" + "x" * 5000).endswith("<redacted-token>")
    assert len(redactor.pending) == 0
    assert redactor.feed("more-secret 正文") == " 正文"

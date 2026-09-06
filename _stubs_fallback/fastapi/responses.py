class StreamingResponse:
    """把生成器收下来放着。真 fastapi 会把它变成 HTTP 分块响应，这里不做。"""

    def __init__(self, content, media_type=None, headers=None, **kw):
        self.body_iterator = content
        self.media_type = media_type
        self.headers = headers or {}

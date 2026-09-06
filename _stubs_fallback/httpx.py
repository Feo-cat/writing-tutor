"""httpx 的**回落替身**——只在真 httpx 没装时才会被 import。

（测试里这个目录是 `sys.path.append` 进去的，不是 `insert(0)`：
 site-packages 排在前面，真装了就一定用真的。）

只替代 `writing_assistant` 真正用到的那一小块：`httpx.stream(...)` 这个上下文管理器，
外加 `Headers` 的大小写不敏感行为（`_gw_note` 靠它读 `X-Gw-Model` 这类响应头）。

⚠️ **它一个网络请求都不发。** `stream()` 直接返回一个 599 的空响应，于是
`_fetch_page_text` 会走它自己那条「非 200 就放弃」的分支，安静地返回 ""。

这是**有意的**：在一个没有网络的环境里，**假装抓到了页面比抓不到更糟**——
前者会让「正文增强」这一步看起来跑过了，而它其实什么都没读到。
和这个仓里那条「没有存档 ≠ 那天没东西」是同一条纪律。
"""


class HTTPError(Exception):
    pass


class TimeoutException(HTTPError):
    pass


class Headers(dict):
    """大小写不敏感的 get——真 httpx.Headers 就是这个行为，普通 dict 不是。"""

    def get(self, key, default=None):
        kl = str(key).lower()
        for k, v in self.items():
            if str(k).lower() == kl:
                return v
        return default


class _Response:
    """既是响应也是上下文管理器（真 httpx.stream 的返回值就这么用）。"""

    def __init__(self, status_code: int = 599):
        self.status_code = status_code      # 599 = 「这里没有网络」，不是 200 也不是 404
        self.headers = Headers()
        self.encoding = "utf-8"
        self.text = ""
        self.content = b""

    def iter_bytes(self, *a, **kw):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def stream(method, url, **kw):
    return _Response()


def get(url, **kw):
    return _Response()

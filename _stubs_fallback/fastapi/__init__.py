"""fastapi 的**回落替身**——只在真 fastapi 没装时才会被 import。"""


class FastAPI:
    def __init__(self, **kw):
        self.kw = kw
        self.routes = []            # [(path, 函数)]，只是记下来，没人会去分发它

    def _route(self, path, **kw):
        def deco(fn):
            self.routes.append((path, fn))
            return fn               # **原样返回**：测试要能直接调这些路由函数
        return deco

    get = post = put = delete = patch = _route

    def mount(self, *a, **kw):
        pass                        # 静态目录：测试里没人访问它

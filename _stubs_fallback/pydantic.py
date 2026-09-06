"""pydantic 的回落替身。见 _stubs_fallback/fastapi/__init__.py 里的说明。"""


class BaseModel:
    """够用就好：字段靠类注解声明，默认值就是类属性，构造时 kwargs 直接塞进实例。

    ⚠️ **不做类型校验，也不查缺字段**——而那两件事恰恰是真 pydantic 的全部价值。
    所以这个替身只够让 server 模块 import 起来，请求体校验的行为它一点都没覆盖。"""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def __repr__(self):
        return f"{type(self).__name__}({self.__dict__})"

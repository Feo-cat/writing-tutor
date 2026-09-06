"""python-dotenv 的假替身——让控制流测试**读不到 .env**。"""
import os


def load_dotenv(*a, **kw):
    """空转。真货会把 .env 读进 os.environ，这里什么都不做。"""
    return False


load_dotenv._is_stub = True   # _test_eval.py 的 A0 靠这个标记确认壳真的盖上了


def dotenv_values(*a, **kw):
    return {}


def find_dotenv(*a, **kw):
    return ""


def get_key(*a, **kw):
    return None


def set_key(*a, **kw):
    """测试绝不该写 .env。真出现了就当场炸，别让它悄悄改开发机上的配置。"""
    raise RuntimeError("stub dotenv：测试里不许写 .env")


def unset_key(*a, **kw):
    raise RuntimeError("stub dotenv：测试里不许改 .env")


__all__ = ["load_dotenv", "dotenv_values", "find_dotenv", "get_key", "set_key", "unset_key"]

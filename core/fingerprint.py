"""设备指纹生成 — OUID 和 jt 参数"""

import hashlib
import random
import string
import time
import uuid


def generate_ouid() -> str:
    raw = uuid.uuid4().hex.upper()
    return f"{raw}:FG=1"


def generate_jt() -> str:
    """生成 jt 指纹参数 — HAR 中格式为 '31$' + base64 编码的设备指纹

    目前使用空值，如果注册被拒再逆向具体算法
    """
    return ""


def random_password(length: int = 12) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    pwd = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice("!@#$%"),
    ]
    pwd += [random.choice(chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)

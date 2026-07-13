"""临时邮箱提供者 — 邮箱 API 直连不走代理，oreateai 注册走代理"""

import logging
import re
import time
import random
import string
import httpx

log = logging.getLogger(__name__)


class OneSecMailProvider:
    """1secmail — API 直连（不走代理，避免 403）"""

    BASE = "https://www.1secmail.com/api/v1/"
    DOMAINS = ["1secmail.com", "1secmail.org", "1secmail.net", "wwjmp.com", "esiix.com"]

    def __init__(self, **_):
        self.http = httpx.Client(timeout=20)  # 直连
        self.address: str = ""
        self._login: str = ""
        self._domain: str = ""

    def create(self, **_) -> str:
        self._domain = random.choice(self.DOMAINS)
        self._login = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
        self.address = f"{self._login}@{self._domain}"
        log.info(f"1secmail created: {self.address}")
        return self.address

    def wait_for_verification_link(self, timeout: int = 180, poll_interval: int = 4) -> tuple[str, str]:
        start = time.time()
        seen_ids = set()
        while time.time() - start < timeout:
            try:
                resp = self.http.get(self.BASE, params={
                    "action": "getMessages",
                    "login": self._login,
                    "domain": self._domain,
                })
                if resp.status_code != 200:
                    log.warning(f"1secmail {resp.status_code}")
                    time.sleep(poll_interval)
                    continue
                messages = resp.json()
            except Exception as e:
                log.warning(f"1secmail poll error: {e}")
                time.sleep(poll_interval)
                continue

            for msg in messages:
                mid = msg["id"]
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
                try:
                    detail = self.http.get(self.BASE, params={
                        "action": "readMessage",
                        "login": self._login,
                        "domain": self._domain,
                        "id": mid,
                    }).json()
                except Exception:
                    continue

                body = detail.get("textBody", "") + detail.get("htmlBody", "")
                token_match = re.search(r"tokenID=([a-f0-9\-]{36})", body)
                if token_match:
                    log.info(f"found tokenID: {token_match.group(1)[:16]}...")
                    return self.address, token_match.group(1)

            elapsed = int(time.time() - start)
            if elapsed > 0 and elapsed % 30 == 0:
                log.info(f"waiting for email... {elapsed}s")
            time.sleep(poll_interval)
        raise TimeoutError(f"mailbox {self.address} no verification email within {timeout}s")

    def close(self):
        self.http.close()


class TempMailProvider:
    """mail.tm — API 直连"""

    BASE = "https://api.mail.tm"

    def __init__(self, **_):
        self.http = httpx.Client(timeout=20)  # 直连
        self.token: str = ""
        self.address: str = ""
        self.password: str = ""

    def create(self, password: str = "Oreate2025!") -> str:
        self.password = password
        domains = self.http.get(f"{self.BASE}/domains").json()
        domain = domains["hydra:member"][0]["domain"]
        local = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        self.address = f"{local}@{domain}"

        self.http.post(f"{self.BASE}/accounts", json={
            "address": self.address, "password": self.password,
        })
        token_resp = self.http.post(f"{self.BASE}/token", json={
            "address": self.address, "password": self.password,
        })
        self.token = token_resp.json()["token"]
        self.http.headers["Authorization"] = f"Bearer {self.token}"
        log.info(f"mail.tm created: {self.address}")
        return self.address

    def wait_for_verification_link(self, timeout: int = 180, poll_interval: int = 3) -> tuple[str, str]:
        start = time.time()
        seen_ids = set()
        while time.time() - start < timeout:
            try:
                messages = self.http.get(f"{self.BASE}/messages").json().get("hydra:member", [])
            except Exception as e:
                log.warning(f"mail.tm poll error: {e}")
                time.sleep(poll_interval)
                continue

            for msg in messages:
                if msg["id"] in seen_ids:
                    continue
                seen_ids.add(msg["id"])
                try:
                    detail = self.http.get(f"{self.BASE}/messages/{msg['id']}").json()
                except Exception:
                    continue
                body = detail.get("text", "") or ""
                html_list = detail.get("html", [])
                html_body = html_list[0] if html_list else ""
                token_match = re.search(r"tokenID=([a-f0-9\-]{36})", body + html_body)
                if token_match:
                    log.info(f"found tokenID: {token_match.group(1)[:16]}...")
                    return self.address, token_match.group(1)
            time.sleep(poll_interval)
        raise TimeoutError(f"mailbox {self.address} no verification email within {timeout}s")

    def close(self):
        self.http.close()


class GuerrillaMailProvider:
    """Guerrilla Mail — 多域名可用，API 直连"""

    BASE = "https://api.guerrillamail.com/ajax.php"

    def __init__(self, **_):
        self.http = httpx.Client(timeout=20)
        self.address: str = ""
        self._sid: str = ""

    def create(self, **_) -> str:
        resp = self.http.get(self.BASE, params={"f": "get_email_address"}).json()
        self.address = resp["email_addr"]
        self._sid = resp["sid_token"]
        log.info(f"guerrilla created: {self.address}")
        return self.address

    def wait_for_verification_link(self, timeout: int = 180, poll_interval: int = 4) -> tuple[str, str]:
        start = time.time()
        seq = 0
        while time.time() - start < timeout:
            try:
                resp = self.http.get(self.BASE, params={
                    "f": "check_email",
                    "sid_token": self._sid,
                    "seq": seq,
                }).json()
                emails = resp.get("list", [])
            except Exception as e:
                log.warning(f"guerrilla poll error: {e}")
                time.sleep(poll_interval)
                continue

            for mail in emails:
                mid = mail.get("mail_id", "")
                try:
                    detail = self.http.get(self.BASE, params={
                        "f": "fetch_email",
                        "sid_token": self._sid,
                        "email_id": mid,
                    }).json()
                except Exception:
                    continue
                body = detail.get("mail_body", "")
                token_match = re.search(r"tokenID=([a-f0-9\-]{36})", body)
                if token_match:
                    log.info(f"found tokenID: {token_match.group(1)[:16]}...")
                    return self.address, token_match.group(1)

            elapsed = int(time.time() - start)
            if elapsed > 0 and elapsed % 30 == 0:
                log.info(f"waiting for email... {elapsed}s")
            time.sleep(poll_interval)
        raise TimeoutError(f"mailbox {self.address} no verification email within {timeout}s")

    def close(self):
        self.http.close()


class LinshiyouxiangProvider:
    """linshiyouxiang.net — 临时邮箱网，直连，域名多且稳"""

    BASE = "https://www.linshiyouxiang.net"

    def __init__(self, proxy: str | None = None):
        import ssl
        from config import DEFAULT_PROXY
        px = proxy or DEFAULT_PROXY
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        transport = httpx.HTTPTransport(proxy=px, verify=ctx) if px else None
        self.http = httpx.Client(
            timeout=20,
            transport=transport,
            verify=ctx,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"},
            follow_redirects=True,
        )
        self.address: str = ""
        self._code: str = ""

    def create(self, **_) -> str:
        import urllib.parse
        resp = self.http.get(self.BASE + "/")
        html = resp.text
        raw = self.http.cookies.get("temp_mail", "")
        self.address = urllib.parse.unquote(raw)
        m = re.search(r'code="([a-f0-9]{64})"', html)
        self._code = m.group(1) if m else ""
        log.info(f"linshiyouxiang created: {self.address} (code={self._code[:16]}...)")
        return self.address

    def wait_for_verification_link(self, timeout: int = 180, poll_interval: int = 4) -> tuple[str, str]:
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = self.http.post(
                    self.BASE + "/get-messages?lang=zh",
                    json={"email": self.address, "code": self._code},
                )
                data = resp.json()
                emails = data.get("emails") or []
            except Exception as e:
                log.warning(f"linshiyouxiang poll error: {e}")
                time.sleep(poll_interval)
                continue

            for mail in emails:
                body = mail.get("Content", "") + mail.get("Subject", "") + str(mail)
                token_match = re.search(r"tokenID=([a-f0-9\-]{36})", body)
                if token_match:
                    log.info(f"found tokenID: {token_match.group(1)[:16]}...")
                    return self.address, token_match.group(1)

                code_attr = mail.get("Code", "")
                if code_attr:
                    detail_resp = self.http.get(f"{self.BASE}/mail/view/{code_attr}")
                    detail_html = detail_resp.text
                    token_match2 = re.search(r"tokenID=([a-f0-9\-]{36})", detail_html)
                    if token_match2:
                        log.info(f"found tokenID in detail: {token_match2.group(1)[:16]}...")
                        return self.address, token_match2.group(1)

            elapsed = int(time.time() - start)
            if elapsed > 0 and elapsed % 20 == 0:
                log.info(f"waiting for email... {elapsed}s")
            time.sleep(poll_interval)
        raise TimeoutError(f"mailbox {self.address} no verification email within {timeout}s")

    def close(self):
        self.http.close()


class AutoEmailProvider:
    """自动选择：linshiyouxiang(最稳) → mail.tm → guerrilla"""

    def __init__(self, **_):
        self._inner = None
        self.address = ""

    def create(self, **_) -> str:
        providers = [LinshiyouxiangProvider, TempMailProvider, GuerrillaMailProvider]
        for cls in providers:
            try:
                self._inner = cls()
                self.address = self._inner.create()
                return self.address
            except Exception as e:
                log.warning(f"{cls.__name__} failed ({e})")
        raise RuntimeError("all email providers failed")

    def wait_for_verification_link(self, timeout: int = 180, poll_interval: int = 4) -> tuple[str, str]:
        return self._inner.wait_for_verification_link(timeout, poll_interval)

    def close(self):
        if self._inner:
            self._inner.close()


class ManualEmailProvider:
    def __init__(self, address: str):
        self.address = address

    def create(self, **_) -> str:
        return self.address

    def wait_for_verification_link(self, **_) -> tuple[str, str]:
        print(f"\n open {self.address} inbox, click verify link")
        token_id = input("tokenID = ").strip()
        return self.address, token_id

    def close(self):
        pass

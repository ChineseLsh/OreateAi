BASE_URL = "https://www.oreateai.com"
CDN_URL = "https://cdn.oreateai.com"

PASSPORT_API = f"{BASE_URL}/passport/api"
OREATE_API = f"{BASE_URL}/oreate"
BIZ_API = f"{BASE_URL}/bizapi"

DEFAULT_PROXY = "http://127.0.0.1:7897"

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/home/index/zh",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
}

REGISTER_POINTS = 80  # 30 daily + 50 welcome
VIDEO_COST = 20

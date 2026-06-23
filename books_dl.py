"""BooksDL - single-module Python port of joytsay/books-dl.

Download e-books you have purchased on Books.com.tw and repackage them as a
standard .epub file.

Faithful port of the original Ruby implementation:
    https://github.com/joytsay/books-dl
"""

import getpass
import hashlib
import json
import os
import posixpath
import random
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from urllib.parse import quote_plus, unquote_plus, urlparse

import requests


class BooksDLError(Exception):
    pass


# --------------------------------------------------------------------- crypto
# The viewer encrypts text resources with a repeating-key XOR. The key is
# derived from the per-book download_token and the resource path via MD5 (to
# pick a split point) + SHA256. Verified byte-exact against upstream fixtures.

# Mirrors the Ruby regex: %r{\Ahttps?://(.*?/){3}.*?(?<rest_part>/.+)\z}
_URL_RE = re.compile(r"https?://(.*?/){3}.*?(?P<rest_part>/.+)\Z")


def _ruby_hex(pair):
    """Mimic Ruby String#hex: parse leading hex digits, 0 if none."""
    m = re.match(r"[0-9a-fA-F]+", pair)
    return int(m.group(0), 16) if m else 0


def hex_to_byte(hex_str):
    """Convert a hex string into a list of byte integers (pairwise)."""
    if not isinstance(hex_str, str):
        return []
    return [_ruby_hex(hex_str[i:i + 2]) for i in range(0, len(hex_str) - 1, 2)]


def generate_key(url, download_token):
    """Derive the XOR key bytes for a given resource URL + download token."""
    if url is None:
        raise ValueError("url is nil")
    if not download_token:
        raise ValueError(f"download_token is nil for url={url!r}")

    if url.startswith(("http://", "https://")):
        match = _URL_RE.match(url)
        if not match or not match.group("rest_part"):
            raise ValueError(f"unexpected download url format: {url}")
        file_path = unquote_plus(match.group("rest_part"))
    else:
        file_path = unquote_plus(url if url.startswith("/") else f"/{url}")

    # MD5 of the path, summed in 4-char (16-bit) chunks, mod 64 -> split point.
    md5_chars = hashlib.md5(file_path.encode("utf-8")).hexdigest()
    partition = 0
    for i in range(0, len(md5_chars), 4):
        partition = (partition + int(md5_chars[i:i + 4], 16)) % 64

    decode_hex = hashlib.sha256(
        (download_token[:partition] + file_path + download_token[partition:]).encode("utf-8")
    ).hexdigest()

    return hex_to_byte(decode_hex)


def decode_xor(key, encrypted_content):
    """Repeating-key XOR decrypt; strips a leading UTF-8 BOM. Returns bytes."""
    if isinstance(encrypted_content, str):
        encrypted_content = encrypted_content.encode("utf-8")

    key_len = len(key)
    out = bytearray(len(encrypted_content))
    count = 0
    for idx, byte in enumerate(encrypted_content):
        out[idx] = byte ^ key[count]
        count += 1
        if count >= key_len:
            count = 0

    if len(out) >= 3 and out[0] == 0xEF and out[1] == 0xBB and out[2] == 0xBF:
        out = out[3:]

    return bytes(out)


def img_checksum():
    """Random checksum sent with image requests (shuffled seed)."""
    # Original seed: %w[0 6 9 3 1 4 7 1 8 0 5 5 9 A A C]
    seed = list("0693147180559AAC")
    random.shuffle(seed)
    return "".join(seed)


# ----------------------------------------------------------------- xml parsing
def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def parse_container(content):
    """Return the OPF root file path from META-INF/container.xml."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        preview = content[:500].decode("utf-8", errors="replace")
        raise BooksDLError(f"Invalid container.xml ({exc}). First 500 chars:\n{preview}")

    for el in root.iter():
        if _local_name(el.tag) == "rootfile" and "full-path" in el.attrib:
            return el.attrib["full-path"]

    preview = content[:500].decode("utf-8", errors="replace")
    raise BooksDLError(f"Invalid container.xml. First 500 chars:\n{preview}")


def parse_opf(path, content):
    """Return (title, [resource paths]) from the OPF root file."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    root = ET.fromstring(content)
    base_dir = posixpath.dirname(path)

    hrefs = [
        posixpath.join(base_dir, el.attrib["href"])
        for el in root.iter()
        if _local_name(el.tag) == "item" and el.attrib.get("href")
    ]
    title = next(
        ((el.text or "").strip() for el in root.iter() if _local_name(el.tag) == "title"),
        "",
    )
    return title, hrefs


# ------------------------------------------------------------------ file record
@dataclass
class File:
    """A single file inside the epub: a path and its (bytes) content."""

    path: str
    content: bytes

    def __post_init__(self):
        if isinstance(self.content, str):
            self.content = self.content.encode("utf-8")


def _safe_filename(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name)


# -------------------------------------------------------------------- main class
class BooksDL:
    """Authenticates, downloads + decrypts every resource, builds the .epub."""

    COOKIE_FILE = "cookie.json"
    IMAGE_EXT = {
        ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".tiff",
        ".tif", ".svg", ".png", ".webp",
    }
    NO_AUTH_EXT = {".css", ".ttc", ".otf", ".ttf", ".eot", ".woff", ".woff2"}

    CART_URL = "https://db.books.com.tw/shopping/cart_list.php"
    LOGIN_HOST = "https://cart.books.com.tw"
    LOGIN_PAGE_URL = f"https://cart.books.com.tw/member/login?url={CART_URL}"
    LOGIN_ENDPOINT_URL = "https://cart.books.com.tw/member/login_do/"

    DEVICE_REG_URL = "https://appapi-ebook.books.com.tw/V1.7/CMSAPIApp/DeviceReg"
    OAUTH_URL = (
        "https://appapi-ebook.books.com.tw/V1.7/CMSAPIApp/LoginURL?type=&device_id="
        "&redirect_uri=https%3A%2F%2Fviewer-ebook.books.com.tw%2Fviewer%2Flogin.html"
    )
    OAUTH_ENDPOINT_URL = "https://appapi-ebook.books.com.tw/V1.7/CMSAPIApp/MemberLogin?code="
    BOOK_DL_URL = "https://appapi-ebook.books.com.tw/V1.7/CMSAPIApp/BookDownLoadURL"

    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_2) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/71.0.3578.98 Safari/537.36"
    )

    def __init__(self, book_id):
        self.book_id = book_id
        self.session = requests.Session()
        self._info = None
        self.cookies = self._load_cookies()
        # Triggers the full login + device-reg + OAuth + token flow.
        self.encoded_token = quote_plus(str(self.info.get("download_token") or ""))

    # ----------------------------------------------------------- orchestration
    def download(self):
        files = [File("mimetype", "application/epub+zip")]

        container = self._step("取得 META-INF/container.xml", lambda: self.fetch("META-INF/container.xml"))
        root_path = parse_container(container)
        files.append(File("META-INF/container.xml", container))

        try:
            enc = self._step("取得 META-INF/encryption.xml", lambda: self.fetch("META-INF/encryption.xml"))
            files.append(File("META-INF/encryption.xml", enc))
        except Exception as exc:  # noqa: BLE001
            print(f"\n{exc}\nJust a encryption file, it doesn't matter...")

        opf = self._step(f"取得 {root_path} 檔案", lambda: self.fetch(root_path))
        title, hrefs = parse_opf(root_path, opf)
        files.append(File(root_path, opf))

        total = len(hrefs)
        for index, path in enumerate(hrefs):
            print(f"{index + 1}/{total} => 開始下載 {path}")
            files.append(File(path, self.fetch(path)))

        filename = _safe_filename(f"{self.book_id}_{title}.epub")
        print("正在製作 epub 檔案...", end="", flush=True)
        with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                # The EPUB spec requires "mimetype" first and uncompressed.
                comp = zipfile.ZIP_STORED if f.path == "mimetype" else zipfile.ZIP_DEFLATED
                zf.writestr(f.path, f.content, compress_type=comp)
        print("成功")

        print(f"{self.book_id} 下載完成")
        return filename

    @staticmethod
    def _step(name, fn):
        print(f"正在{name}...", end="", flush=True)
        result = fn()
        print("成功")
        return result

    # ------------------------------------------------------------------- fetch
    def fetch(self, path):
        url = f"{self.info['download_link']}{path}"
        ext = os.path.splitext(path)[1].lower()

        if ext in self.NO_AUTH_EXT or self.info.get("encrypt_type") == "none":
            return self._get(url).content

        if ext in self.IMAGE_EXT:
            checksum = img_checksum()
            return self._get(f"{url}?checksum={checksum}&DownloadToken={self.encoded_token}").content

        key = generate_key(url, self.info["download_token"])
        return decode_xor(key, self._get(f"{url}?DownloadToken={self.encoded_token}").content)

    # -------------------------------------------------------------------- info
    @property
    def info(self):
        if self._info is not None:
            return self._info

        self.login()

        data = {
            "device_id": "2b2475e7-da58-4cfe-aedf-ab4e6463757b",
            "language": "zh-TW",
            "os_type": "WEB",
            "os_version": self.USER_AGENT,
            "screen_resolution": "1680X1050",
            "screen_dpi": "96",
            "device_vendor": "Google Inc.",
            "device_model": "web",
        }
        headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://viewer-ebook.books.com.tw",
            "Referer": (
                "https://viewer-ebook.books.com.tw/viewer/epub/web/"
                "?book_uni_id=E050017049_reflowable_normal"
            ),
        }

        for key in ("CmsToken", "redirect_uri", "normal_redirect_uri", "DownloadToken"):
            self.cookies.pop(key, None)

        print("註冊 Fake device 中...")
        self._post(self.DEVICE_REG_URL, data, headers)

        print("透過 OAuth 取得 CmsToken...")
        login_uri = json.loads(self._get(self.OAUTH_URL).text)["login_uri"]
        code = self._get(login_uri).headers["Location"].split("&code=")[-1]
        self._get(f"{self.OAUTH_ENDPOINT_URL}{code}")

        resp = self._get(f"{self.BOOK_DL_URL}?book_uni_id={self.book_id}&t={int(time.time())}")
        self._info = json.loads(resp.text)
        return self._info

    # ------------------------------------------------------------------ login
    def login(self):
        if self.logged():
            return

        if self._login_with_slider_captcha():
            print("🎉 使用 Playwright 自動登入成功")
            return

        print("⚠️ Playwright 失敗，改用人工輸入驗證碼模式")
        username = input("請輸入帳號：").strip()
        password = getpass.getpass("請輸入密碼:").strip()
        login_page = self._get(self.LOGIN_PAGE_URL).text
        captcha = self._get_captcha_from(login_page)

        data = {"captcha": captcha, "login_id": username, "login_pswd": password}
        headers = {
            "Host": "cart.books.com.tw",
            "Referer": "https://cart.books.com.tw/member/login",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }
        self._post(self.LOGIN_ENDPOINT_URL, data, headers)

        if self.logged():
            return
        print(f"{'-' * 10} 登入失敗，請再試一次 {'-' * 10}\n")
        self.login()

    def logged(self):
        return self._get(self.CART_URL).status_code == 200

    # ----------------------------------------------------------- http + cookies
    def _load_cookies(self):
        try:
            with open(self.COOKIE_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_cookies(self, response):
        for cookie in response.cookies:
            self.cookies[cookie.name] = cookie.value
        with open(self.COOKIE_FILE, "w", encoding="utf-8") as fh:
            json.dump(self.cookies, fh, ensure_ascii=False, indent=2)

    def _headers(self, extra=None):
        h = {"user-agent": self.USER_AGENT,
             "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
        if extra:
            h.update(extra)
        return h

    def _get(self, url, headers=None):
        # allow_redirects=False mirrors the Ruby HTTP gem; the OAuth step reads
        # the 302 Location header directly, and logged() relies on a 200 check.
        self.session.cookies.clear()
        resp = self.session.get(url, headers=self._headers(headers), allow_redirects=False)
        if resp.status_code >= 400:
            name = urlparse(url).path.rsplit("/", 1)[-1]
            raise BooksDLError(f"取得 `{name}` 失敗。 Status: {resp.status_code}")
        self._save_cookies(resp)
        return resp

    def _post(self, url, data=None, headers=None):
        self.session.cookies.clear()
        resp = self.session.post(url, data=data or {}, headers=self._headers(headers), allow_redirects=False)
        self._save_cookies(resp)
        return resp

    # ------------------------------------------------------- manual captcha
    def _get_captcha_from(self, login_page):
        match = re.search(
            r'id=["\']captcha_img["\'][^>]*>.*?<img[^>]*src=["\']([^"\']+)["\']',
            login_page, re.DOTALL,
        ) or re.search(r'<img[^>]*src=["\']([^"\']*captcha[^"\']*)["\']', login_page)
        if not match:
            raise BooksDLError("找不到驗證碼圖片 (captcha image not found)")

        img = self._get(f"{self.LOGIN_HOST}{match.group(1)}").content
        with open("captcha.png", "wb") as fh:
            fh.write(img)
        self._open_file("captcha.png")
        return input("請輸入認證碼 (captcha.png，不分大小寫)：").strip()

    @staticmethod
    def _open_file(path):
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            elif sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception:
            print("開啟失敗，請自行查看 captcha.png 檔案。")

    # ------------------------------------------------------- playwright login
    def _login_with_slider_captcha(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False

        launch_opts = {
            "headless": False,
            "args": [
                "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                "--disable-gpu", "--disable-software-rasterizer",
            ],
        }
        # Use a system browser instead of Playwright's bundled Chromium:
        #   CHROME_BINARY=/path/to/chrome   or   CHROME_CHANNEL=chrome|msedge|...
        binary = os.environ.get("CHROME_BINARY")
        channel = os.environ.get("CHROME_CHANNEL")
        if binary:
            launch_opts["executable_path"] = binary
        elif channel:
            launch_opts["channel"] = channel

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(**launch_opts)
                try:
                    context = browser.new_context(
                        viewport={"width": 1280, "height": 800},
                        user_agent=self.USER_AGENT,
                    )
                    context.new_page().goto(self.LOGIN_PAGE_URL)
                    input("請在瀏覽器中手動輸入帳號、密碼並完成滑塊驗證，完成後請按 Enter 繼續...")
                    for cookie in context.cookies():
                        self.cookies[cookie["name"]] = cookie["value"]
                finally:
                    browser.close()

            with open(self.COOKIE_FILE, "w", encoding="utf-8") as fh:
                json.dump(self.cookies, fh, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[Playwright] 登入失敗：{type(exc).__name__} - {exc}")
            print("提示：首次使用請先執行 `playwright install chromium`")
            return False

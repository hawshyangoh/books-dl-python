"""Port of lib/books_dl/api.rb.

Handles authentication (Selenium-assisted slider captcha, or manual captcha
fallback), fake-device registration, the OAuth handshake that yields a
``CmsToken``, fetching the per-book download token, and downloading +
decrypting individual resources.
"""

import getpass
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote_plus, urlparse

import requests

from .utils import Utils


class BooksDLError(Exception):
    pass


class _Info:
    """Lightweight stand-in for Ruby's OpenStruct over the BookDownLoadURL JSON."""

    def __init__(self, data):
        self._data = data or {}

    def __getattr__(self, name):
        return self._data.get(name)


class API:
    COOKIE_FILE_NAME = "cookie.json"
    IMAGE_EXTENSIONS = {
        ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".tiff",
        ".tif", ".svg", ".png", ".webp",
    }
    NO_AUTH_EXTENSIONS = {".css", ".ttc", ".otf", ".ttf", ".eot", ".woff", ".woff2"}

    # API endpoints
    CART_URL = "https://db.books.com.tw/shopping/cart_list.php"
    LOGIN_HOST = "https://cart.books.com.tw"
    LOGIN_PAGE_URL = f"https://cart.books.com.tw/member/login?url={CART_URL}"
    LOGIN_ENDPOINT_URL = "https://cart.books.com.tw/member/login_do/"

    DEVICE_REG_URL = "https://appapi-ebook.books.com.tw/V1.7/CMSAPIApp/DeviceReg"
    OAUTH_URL = (
        "https://appapi-ebook.books.com.tw/V1.7/CMSAPIApp/LoginURL?type=&device_id="
        "&redirect_uri=https%3A%2F%2Fviewer-ebook.books.com.tw%2Fviewer%2Flogin.html"
    )
    OAUTH_ENDPOINT_URL = (
        "https://appapi-ebook.books.com.tw/V1.7/CMSAPIApp/MemberLogin?code="
    )
    BOOK_DL_URL = "https://appapi-ebook.books.com.tw/V1.7/CMSAPIApp/BookDownLoadURL"

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_2) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/71.0.3578.98 Safari/537.36"
    )

    def __init__(self, book_id):
        self.book_id = book_id
        self.session = requests.Session()
        self._info = None
        self._logged = None
        self._load_existed_cookies()
        # Triggers the full login + device-reg + OAuth + token flow (as in Ruby).
        self.encoded_token = quote_plus(str(self.info.download_token or ""))

    # ------------------------------------------------------------------ fetch
    def fetch(self, path):
        url = f"{self.info.download_link}{path}"
        ext = os.path.splitext(path)[1].lower()

        if ext in self.NO_AUTH_EXTENSIONS or self.info.encrypt_type == "none":
            return self._get(url).content

        if ext in self.IMAGE_EXTENSIONS:
            checksum = Utils.img_checksum()
            resp = self._get(
                f"{url}?checksum={checksum}&DownloadToken={self.encoded_token}"
            )
            return resp.content

        key = Utils.generate_key(url, self.info.download_token)
        resp = self._get(f"{url}?DownloadToken={self.encoded_token}")
        return Utils.decode_xor(key, resp.content)

    # ------------------------------------------------------------------- info
    @property
    def info(self):
        if self._info is not None:
            return self._info

        self.login()

        data = {
            "device_id": "2b2475e7-da58-4cfe-aedf-ab4e6463757b",
            "language": "zh-TW",
            "os_type": "WEB",
            "os_version": self.DEFAULT_USER_AGENT,
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

        # Remove stale cookies before re-registering the device.
        for key in ("CmsToken", "redirect_uri", "normal_redirect_uri", "DownloadToken"):
            self.current_cookie.pop(key, None)

        print("註冊 Fake device 中...")
        self._post(self.DEVICE_REG_URL, data, headers)

        print("透過 OAuth 取得 CmsToken...")
        resp = self._get(self.OAUTH_URL)
        login_uri = json.loads(resp.text)["login_uri"]
        location = self._get(login_uri).headers["Location"]
        code = location.split("&code=")[-1]
        self._get(f"{self.OAUTH_ENDPOINT_URL}{code}")

        resp = self._get(f"{self.BOOK_DL_URL}?book_uni_id={self.book_id}&t={int(time.time())}")
        self._info = _Info(json.loads(resp.text))
        return self._info

    # ------------------------------------------------------------------ login
    def login(self):
        if self.logged():
            return

        # Try Selenium-assisted login first (handles the slider captcha).
        if self._login_with_slider_captcha():
            print("🎉 使用 Selenium 自動登入成功")
            return

        print("⚠️ Selenium 失敗，改用人工輸入驗證碼模式")
        username, password = self._get_account_from_stdin()
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
        response = self._get(self.CART_URL)
        self._logged = response.status_code == 200
        return self._logged

    # ----------------------------------------------------------- cookie state
    def _load_existed_cookies(self):
        try:
            with open(self.COOKIE_FILE_NAME, "r", encoding="utf-8") as fh:
                self.current_cookie = json.load(fh)
        except Exception:
            self.current_cookie = {}

    def _save_cookie(self, response):
        for cookie in response.cookies:
            self.current_cookie[cookie.name] = cookie.value
        with open(self.COOKIE_FILE_NAME, "w", encoding="utf-8") as fh:
            json.dump(self.current_cookie, fh, ensure_ascii=False, indent=2)

    def _cookie_header(self):
        return "; ".join(f"{name}={value}" for name, value in self.current_cookie.items())

    # --------------------------------------------------------------- requests
    def _default_headers(self):
        return {"user-agent": self.DEFAULT_USER_AGENT}

    def _build_headers(self, *layers):
        headers = self._default_headers()
        for layer in layers:
            headers.update(layer)
        return headers

    def _get(self, url, headers=None):
        h = self._build_headers({"Cookie": self._cookie_header()}, headers or {})
        # allow_redirects=False mirrors the Ruby HTTP gem (no auto-follow); the
        # OAuth step depends on reading the 302 Location header directly.
        self.session.cookies.clear()
        response = self.session.get(url, headers=h, allow_redirects=False)
        if response.status_code >= 400:
            file_name = urlparse(url).path.rsplit("/", 1)[-1]
            raise BooksDLError(f"取得 `{file_name}` 失敗。 Status: {response.status_code}")
        self._save_cookie(response)
        return response

    def _post(self, url, data=None, headers=None):
        h = self._build_headers({"Cookie": self._cookie_header()}, headers or {})
        self.session.cookies.clear()
        response = self.session.post(url, data=data or {}, headers=h, allow_redirects=False)
        self._save_cookie(response)
        return response

    # ------------------------------------------------------ manual login bits
    def _get_account_from_stdin(self):
        username = input("請輸入帳號：")
        password = getpass.getpass("請輸入密碼:")
        return username.strip(), password.strip()

    def _get_captcha_from(self, login_page):
        match = re.search(
            r'id=["\']captcha_img["\'][^>]*>.*?<img[^>]*src=["\']([^"\']+)["\']',
            login_page,
            re.DOTALL,
        )
        if not match:
            match = re.search(r'<img[^>]*src=["\']([^"\']*captcha[^"\']*)["\']', login_page)
        if not match:
            raise BooksDLError("找不到驗證碼圖片 (captcha image not found)")

        captcha_img_url = f"{self.LOGIN_HOST}{match.group(1)}"
        img = self._get(captcha_img_url).content
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

    # ------------------------------------------------------ selenium login
    def _login_with_slider_captcha(self):
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except ImportError:
            return False

        options = Options()
        binary = os.environ.get("CHROME_BINARY")
        if binary:
            options.binary_location = binary
        for arg in (
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--window-size=1280,800",
        ):
            options.add_argument(arg)

        driver_path = os.environ.get("CHROMEDRIVER")
        service = Service(executable_path=driver_path) if driver_path else None

        driver = None
        try:
            driver = webdriver.Chrome(options=options, service=service)
            driver.get(self.LOGIN_PAGE_URL)
            input("請在瀏覽器中手動輸入帳號、密碼並完成滑塊驗證，完成後請按 Enter 繼續...")

            for cookie in driver.get_cookies():
                self.current_cookie[cookie["name"]] = cookie["value"]

            with open(self.COOKIE_FILE_NAME, "w", encoding="utf-8") as fh:
                json.dump(self.current_cookie, fh, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[Selenium] 登入失敗：{type(exc).__name__} - {exc}")
            return False
        finally:
            if driver is not None:
                driver.quit()

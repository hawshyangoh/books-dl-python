# books-dl (Python)

Python port of [joytsay/books-dl](https://github.com/joytsay/books-dl). Downloads
e-books **you have already purchased** on Books.com.tw and repackages them as a
standard `.epub`.

> 僅供個人非商業用途，請先至網站購買電子書。下載期間請勿在 Books.com.tw 進行其他瀏覽器操作
> （電子書區不允許多裝置同時登入）。

> ✅ **Working as of 2026-06-23** — verified end-to-end against a real purchased
> book (full 309-file title → ~42 MB, zip-validated `.epub`). The crypto port is
> byte-exact against the upstream RSpec fixtures.

## Requirements

- Python 3.9+
- [`curl_cffi`](https://pypi.org/project/curl-cffi/) — required
- [`playwright`](https://pypi.org/project/playwright/) — optional (automated login)
- Google Chrome installed (recommended, for the login browser)

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # only if using the automated login
```

- **curl_cffi** impersonates a real Chrome TLS handshake — required to get past
  Cloudflare's bot protection on the OAuth host (plain `requests` gets a 403).
- **playwright** is only needed for the automated slider-captcha login. If it's
  not installed, the tool falls back to a manual image-captcha prompt.

## Get a `book_id`

Open the book's reading page and copy the `book_uni_id` query parameter from the URL:

```
https://viewer-ebook.books.com.tw/viewer/epub/web/?book_uni_id=E050096232_reflowable_normal&ran=97991701
                                                                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ this is the book_id
```

## Run

```bash
python main.py E050096232_reflowable_normal
# or edit DEFAULT_BOOK_ID in main.py and run: python main.py
```

On first run a Chrome window opens: log in and complete the slider captcha
manually, then press **Enter** in the terminal. Session cookies are cached to
`cookie.json` (and the browser User-Agent to `.books_ua`) so later runs reuse the
session. The finished `.epub` is written to the current directory as
`<book_id>_<title>.epub`.

## How it works

1. **Login** – Playwright-assisted (slider captcha) or manual captcha fallback.
   The browser's User-Agent is captured so the HTTP layer matches the Cloudflare
   `cf_clearance` cookie.
2. **DeviceReg** – registers a device (as `Windows`/etc., **not** `WEB`).
3. **OAuth** – via curl_cffi (Chrome TLS impersonation, to pass Cloudflare on
   `cart.books.com.tw`), obtains a `CmsToken` and per-book `download_token`.
4. **Download** – fetches `META-INF/container.xml`, the OPF root file, and every
   manifest resource. Text resources are decrypted with a repeating-key XOR whose
   key is derived (MD5 split-point + SHA256) from the `download_token` and the
   resource path. Images/fonts/CSS are fetched as-is.
5. **Package** – zips everything into a spec-compliant `.epub` (`mimetype` stored
   first, uncompressed).

## Configuration (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `BOOKS_OS_TYPE` | `Windows` | Device type registered with the API. `WEB` is **blocked** by Books.com.tw; use `Windows`, `MAC`, `iOS`, or `APP`. |
| `BOOKS_USER_AGENT` | captured at login | HTTP User-Agent. Must match the `cf_clearance` cookie; normally auto-captured and cached in `.books_ua`. |
| `BOOKS_IMPERSONATE` | `chrome` | curl_cffi TLS impersonation target. |
| `CHROME_BINARY` | — | Path to a Chrome/Chromium executable for the login browser. |
| `CHROME_CHANNEL` | `chrome` | Playwright channel, e.g. `chrome`, `msedge`, `chromium-beta`. |

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Login button / slider won't respond | The site detects automation. The tool launches real Chrome with automation flags stripped; make sure Google Chrome is installed (or set `CHROME_BINARY`). |
| `取得 \`oauth\` 失敗。 Status: 403` | Cloudflare blocking a non-browser TLS fingerprint. Ensure `curl_cffi` is installed (it's required). |
| `web device has no permission` | `os_type=WEB` is no longer allowed. The default is now `Windows`; override with `BOOKS_OS_TYPE` if needed. |
| `Device not Registered` | Stale session — delete `cookie.json` and log in again. |
| Repeated browser logins | `cookie.json`/`.books_ua` missing or expired; a fresh login refreshes them. |

> **Device limit:** the registered device id is shared/hardcoded. Books.com.tw
> limits concurrent devices per account, so downloading here consumes a device
> slot. If the official app later asks you to remove a device, that's why.

## Project layout

```
books_dl.py   # everything: crypto, XML parsing, auth/OAuth, fetch+decrypt, epub build
main.py       # CLI entry point
```

`books_dl.py` exposes:

- `generate_key`, `hex_to_byte`, `decode_xor`, `img_checksum` — crypto helpers
- `parse_container`, `parse_opf` — XML parsing
- `BooksDL(book_id).download()` — the full flow

The crypto port is byte-exact against the upstream RSpec fixtures
(`generate_key`, `hex_to_byte`, `decode_xor`).

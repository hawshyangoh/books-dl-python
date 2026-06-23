# books-dl (Python)

Python port of [joytsay/books-dl](https://github.com/joytsay/books-dl). Downloads
e-books **you have already purchased** on Books.com.tw and repackages them as a
standard `.epub`.

> 僅供個人非商業用途，請先至網站購買電子書。下載期間請勿在 Books.com.tw 進行其他瀏覽器操作
> （電子書區不允許多裝置同時登入）。

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # requests (required), playwright (optional)
playwright install chromium        # only if using the automated login
```

`playwright` is only needed for the automated slider-captcha login. If it's not
installed, the tool falls back to a manual image-captcha prompt.

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

On first run a browser opens (Playwright path): log in and complete the slider
captcha manually, then press **Enter** in the terminal. Session cookies are
cached to `cookie.json` for subsequent runs. The finished `.epub` is written to
the current directory.

### Playwright / Chrome options

By default Playwright uses its own bundled Chromium (`playwright install chromium`).
To use a system browser instead, set one of:

- `CHROME_BINARY` — path to a Chrome/Chromium executable.
- `CHROME_CHANNEL` — a Playwright channel, e.g. `chrome`, `msedge`, `chromium-beta`.

## How it works

1. **Login** – Playwright-assisted (slider captcha) or manual captcha fallback.
2. **DeviceReg** – registers a fake web device.
3. **OAuth** – obtains a `CmsToken` and per-book `download_token`.
4. **Download** – fetches `META-INF/container.xml`, the OPF root file, and every
   manifest resource. Text resources are decrypted with a repeating-key XOR whose
   key is derived (MD5 split-point + SHA256) from the `download_token` and the
   resource path. Images/fonts/CSS are fetched as-is.
5. **Package** – zips everything into a spec-compliant `.epub` (`mimetype` stored
   first, uncompressed).

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

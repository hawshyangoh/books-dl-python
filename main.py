"""Port of main.rb.

如何取得 book_id:
    進入你要下載的書的閱讀頁面，取得網址列中網址，例如：
    https://viewer-ebook.books.com.tw/viewer/epub/web/?book_uni_id=E050096232_reflowable_normal&ran=97991701
    book_uni_id= 之後的字串就是這本書的 book_id。

用法:
    python main.py                       # 使用下方預設的 book_id
    python main.py E050260238_reflowable_trial
"""

import sys

from books_dl import Downloader

DEFAULT_BOOK_ID = "E050260238_reflowable_trial"


def main():
    book_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BOOK_ID
    downloader = Downloader(book_id)
    downloader.perform()


if __name__ == "__main__":
    main()

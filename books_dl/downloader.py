"""Port of lib/books_dl/downloader.rb.

Orchestrates the download: container.xml -> encryption.xml -> OPF root file ->
all manifest resources, then zips everything into a valid .epub.
"""

import re
import zipfile

from .api import API
from .base_file import BaseFile
from .files import Container, Content


class Downloader:
    def __init__(self, book_id):
        self.book_id = book_id
        self.api = API(book_id)
        self.book = {
            "root_file_path": None,
            "root_file": None,
            "files": [BaseFile("mimetype", "application/epub+zip")],
        }

    def perform(self):
        self._job("取得 META-INF/container.xml", self._fetch_container_file)
        self._job("取得 META-INF/encryption.xml", self._fetch_encryption_file)
        self._job(f"取得 {self.book['root_file_path']} 檔案", self._fetch_root_file)
        self._fetch_book_content()  # prints its own progress
        self._job("製作 epub 檔案", self._build_epub)

        print(f"{self.book_id} 下載完成")

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _job(name, fn):
        print(f"正在{name}...", end="", flush=True)
        if fn():
            print("成功")
        else:
            print()

    def _fetch_container_file(self):
        path = "META-INF/container.xml"
        content = self.api.fetch(path)
        container_file = Container(path, content)

        self.book["root_file_path"] = container_file.root_file_path
        self.book["files"].append(container_file)
        return True

    def _fetch_encryption_file(self):
        path = "META-INF/encryption.xml"
        try:
            content = self.api.fetch(path)
        except Exception as exc:  # noqa: BLE001
            print(f"\n{exc}")
            print("Just a encryption file, it doesn't matter...")
            return False
        self.book["files"].append(BaseFile(path, content))
        return True

    def _fetch_root_file(self):
        path = self.book["root_file_path"]
        content = self.api.fetch(path)
        root_file = Content(path, content)

        self.book["root_file"] = root_file
        self.book["files"].append(root_file)
        return True

    def _fetch_book_content(self):
        root_file = self.book["root_file"]
        file_paths = root_file.file_paths()

        total = len(file_paths)
        for index, path in enumerate(file_paths):
            print(f"{index + 1}/{total} => 開始下載 {path}")
            content = self.api.fetch(path)
            self.book["files"].append(BaseFile(path, content))

    def _build_epub(self):
        title = self.book["root_file"].title()
        files = self.book["files"]
        filename = self._safe_filename(f"{self.book_id}_{title}.epub")

        with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                # The EPUB spec requires "mimetype" first and uncompressed.
                compress = zipfile.ZIP_STORED if f.path == "mimetype" else zipfile.ZIP_DEFLATED
                zf.writestr(f.path, f.content, compress_type=compress)
        return True

    @staticmethod
    def _safe_filename(name):
        return re.sub(r'[\\/:*?"<>|]', "_", name)

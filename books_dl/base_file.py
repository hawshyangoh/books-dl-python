"""Port of lib/books_dl/base_file.rb."""


class BaseFile:
    """A single file inside the epub: a path and its (bytes) content."""

    def __init__(self, path, content):
        self.path = path
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.content = content

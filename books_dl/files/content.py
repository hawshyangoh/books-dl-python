"""Port of lib/books_dl/files/content.rb.

Parses the OPF root file: lists all manifest <item> hrefs (resolved against
the OPF's directory) and extracts the book <title>.
"""

import posixpath
import xml.etree.ElementTree as ET

from ..base_file import BaseFile


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


class Content(BaseFile):
    def _doc(self):
        return ET.fromstring(self.content)

    def file_paths(self):
        root = self._doc()
        base_dir = posixpath.dirname(self.path)
        paths = []
        for el in root.iter():
            if _local_name(el.tag) == "item":
                href = el.attrib.get("href")
                if href:
                    paths.append(posixpath.join(base_dir, href))
        return paths

    def title(self):
        root = self._doc()
        for el in root.iter():
            if _local_name(el.tag) == "title":
                return (el.text or "").strip()
        return ""

"""Port of lib/books_dl/files/container.rb.

Parses META-INF/container.xml to find the OPF root file path.
"""

import xml.etree.ElementTree as ET

from ..base_file import BaseFile


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


class Container(BaseFile):
    def __init__(self, path, content):
        super().__init__(path, content)

        try:
            root = ET.fromstring(self.content)
        except ET.ParseError as exc:
            preview = self.content[:500].decode("utf-8", errors="replace")
            raise ValueError(
                f"Invalid container.xml ({exc}). First 500 chars:\n{preview}"
            )

        rootfile = None
        for el in root.iter():
            if _local_name(el.tag) == "rootfile":
                rootfile = el
                break

        if rootfile is None or "full-path" not in rootfile.attrib:
            preview = self.content[:500].decode("utf-8", errors="replace")
            raise ValueError(f"Invalid container.xml. First 500 chars:\n{preview}")

        self.root_file_path = rootfile.attrib["full-path"]

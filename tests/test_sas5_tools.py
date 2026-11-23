"""Len's SAS5 tools, on small files built here."""

import importlib.util
import struct
import unittest
from pathlib import Path

from PIL import Image

TOOLS = (
    Path(__file__).resolve().parents[1]
    / "backend/dazedtl/engine/data/skills/game-translation/tools/C++/SAS5"
)


def tool(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


text, iar = tool("sas5_text"), tool("sas5_iar")
LINE = "あ".encode("cp932")


def u32(value):
    return struct.pack("<I", value)


def command(data):
    return b"\x1b\x02\x00\x01" + struct.pack("<H", len(data)) + data + b"\xff"


def sec5(direct=None):
    """A file and its library's offset: a jump to the library, a string, a
    message, a relative address of it, then a library script that calls the
    string's command and is the exported handler."""

    def code(target):
        first = command(b"\x70" + u32(target) + b"\xff")
        string = command(b"\x7a\x00" + bytes([len(LINE)]) + LINE + b"\xff")
        message = len(first) + len(string)
        head = first + string + LINE + text.BREAK + LINE
        operand_end = len(head) + 6 + 1 + 4
        head += command(b"\x12" + u32((message - operand_end) & 0xFFFFFFFF) + b"\xff")
        return head + b"\x0e" + u32(len(first)) + b"\x11", len(head)

    library = code(0)[1]
    data = code(library if direct is None else direct)[0]
    option = b"\x00" + u32(2)
    for name, value in (
        (b"EXPORT_FUNS", b"\x00" + u32(1) + u32(1) + b"f\x02" + u32(library)),
        (b"Title", b"\x03" + u32(len(LINE)) + LINE),
    ):
        option += u32(len(name)) + name + value
    czit = u32(2) + b"a.sal\0" + u32(0) + b"lib.sal\0" + u32(library)
    out = b"SEC5" + u32(110000)
    for tag, body in (
        (b"CHAR", bytes(16)),
        (b"OPTN", option),
        (b"CZIT", czit),
        (b"DTDE", u32(0)),
        (b"CODE", text.cipher(data, encode=True)),
        (b"ENDS", b""),
    ):
        out += tag + u32(len(body)) + body
    return out, library


class Sas5TextTests(unittest.TestCase):
    def test_longer_text_moves_every_address(self):
        raw, library = sec5()
        old = text.Sec5(raw)
        self.assertEqual(old.rebuild({}), raw)
        units = {unit["kind"]: unit["id"] for unit in old.units()}
        translations = {
            units["message"]: "い" * 20 + "\n" + "う" * 9 + "\n" + "え",
            units["string"]: "お" * 30,
            "OPTN/Title": "Longer title",
        }
        new = text.Sec5(old.rebuild(translations, 932))
        self.assertEqual(new.problems(), [])
        moved = new.scripts[1][1]
        self.assertGreater(moved, library)
        self.assertEqual(new.exports(), [("/EXPORT_FUNS/f", moved)])
        self.assertEqual(
            [new.code.target(*row) for row in new.code.addresses],
            [moved, new.code.texts[0][0], old.code.target(*old.code.addresses[2])],
        )
        self.assertEqual(new.codepage, 932)
        found = {unit["kind"]: unit["text"] for unit in new.units()}
        self.assertEqual(found["string"], translations[units["string"]])
        self.assertEqual(found["message"], translations[units["message"]])

    def test_older_files_jump_through_19_with_wider_lengths(self):
        head = b"\x1b\x02\x00\x01" + u32(6) + b"\x19" + u32(0) + b"\xff\xff" + LINE
        end = len(head)
        data = head.replace(b"\x19" + u32(0), b"\x19" + u32(end)) + b"\x1b\x04\x00\xff"
        out = b"SEC5" + u32(107000)
        for tag, body in (
            (b"CHAR", bytes(16)),
            (b"OPTN", b"\x00" + u32(0)),
            (b"CZIT", u32(1) + b"a.sal\0" + u32(0)),
            (b"DTDE", u32(0)),
            (b"CODE", text.cipher(data, encode=True)),
        ):
            out += tag + u32(len(body)) + body
        old = text.Sec5(out)
        self.assertEqual(old.rebuild({}), out)
        new = text.Sec5(old.rebuild({old.units()[0]["id"]: "い" * 9}))
        self.assertEqual(new.code.target(*new.code.addresses[0]), end + 16)

    def test_refuses_what_the_engine_could_not_read(self):
        with self.assertRaisesRegex(text.SAS5Error, "Unmapped"):
            text.Sec5(sec5(direct=3)[0]).rebuild({})
        old = text.Sec5(sec5()[0])
        string = next(u["id"] for u in old.units() if u["kind"] == "string")
        for bad in ("お" * 128, "a\x1b", "\U0001f600"):
            with self.assertRaises(text.SAS5Error):
                old.rebuild({string: bad})


class Sas5IarTests(unittest.TestCase):
    def archive(self):
        entries = []
        for flags, depth in ((0x02, 1), (0x1C, 3), (0x3C, 4)):
            stride = (5 * depth + 3) // 4 * 4
            size = stride * 3
            head = struct.pack("<HBBIIIII", flags, 0, 0, 0, size, 0, size, 0)
            head += struct.pack("<iiIII", -4, 7, 5, 3, stride)
            entries.append(head.ljust(0x48, b"\x05") + bytes(size))
        offsets, at = b"", 0x20 + 8 * len(entries)
        for entry in entries:
            offsets += struct.pack("<Q", at)
            at += len(entry)
        head = b"iar " + struct.pack("<HH", 4, 1) + bytes(16) + u32(3) + u32(3)
        return head + offsets + b"".join(entries)

    def test_packs_each_depth_and_keeps_other_entries(self):
        old = iar.Archive(self.archive())
        for index, (mode, depth) in enumerate((("L", 1), ("RGB", 3), ("RGBA", 4))):
            picture = Image.frombytes(mode, (5, 3), bytes(range(15 * depth)))
            new = iar.Archive(old.packed({index: picture}))
            changed = new.entries[index]
            self.assertEqual(changed.image().tobytes(), picture.tobytes())
            self.assertEqual((changed.x, changed.y), (-4, 7))
            for other in {0, 1, 2} - {index}:
                self.assertEqual(new.entries[other].data, old.entries[other].data)
        with self.assertRaises(iar.IarError):
            old.packed({2: Image.new("RGBA", (6, 3))})

    def test_compression_reads_back_at_every_offset_and_length_code(self):
        noise, seed = bytearray(), 1
        for _ in range(0x2000):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            noise.append(seed >> 16 & 0xFF)
        for offset in (1, 0x200, 0x201, 0x401, 0x801, 0x1000, 0x1001, 0x2000):
            data = bytes(noise[:offset])
            for length in (3, 6, 7, 8, 9, 16, 17, 272):
                data += (data[-offset:] * (length // offset + 1))[:length] + b"\0\xff"
            self.assertEqual(iar.decompress(iar.compress(data), len(data)), data)
        # A stream by hand: two literals, then each kind of match, so that an
        # encoder and decoder wrong alike still fail.
        # Bits, low first: 1 1 | 0 1 0 1 1 (offset 2, 3 bytes) | 0 0 0 (2 bytes).
        bits = [1, 1, 0, 1, 0, 1, 1, 0, 0, 0]
        stream = struct.pack("<H", sum(bit << n for n, bit in enumerate(bits)))
        stream += b"ab" + b"\x01" + b"\x00"
        self.assertEqual(iar.decompress(stream, 7), b"ababa" + b"aa")
        with self.assertRaises(iar.IarError):
            iar.decompress(stream, 50)


if __name__ == "__main__":
    unittest.main()

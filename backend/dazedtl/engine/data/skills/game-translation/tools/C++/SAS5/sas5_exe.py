"""Extract and patch UI strings of SAS5 (solfasys) Windows executables.

Reads SAS5's embedded GNU gettext UI catalog (`GMO` resource, which holds the
pause menu, Settings, save/load, backlog, confirmations and errors), Win32 PE
resources (`RT_STRING`, `RT_MENU`, `RT_DIALOG`), and static UTF-16LE / cp932
Japanese strings in initialized data sections. Needs only Python's standard
library.

  sas5_exe.py info   <game.exe>
  sas5_exe.py check  <game.exe>
  sas5_exe.py decode <game.exe> <out dir>
  sas5_exe.py units  <game.exe> <units.json>
  sas5_exe.py patch  <original.exe> <translations.json> <out.exe>

`check` verifies that every UI resource parses cleanly and rebuilds identically
with no edits. `decode` writes extracted Japanese UI strings as UTF-8 for
DazedTL's census (`census --decoded`), so coverage counts the exe's UI
alongside the .sec5 scenario.

Unit IDs:
  RSRC/GMO/<res>/<index>      GNU gettext .mo UI string (arbitrary length; carries msgid)
  RSRC/STRING/<id>            Win32 string table entry (arbitrary length)
  RSRC/MENU/<res>/<index>     Win32 menu caption (arbitrary length)
  RSRC/DIALOG/<res>/title     Win32 dialog title (arbitrary length)
  RSRC/DIALOG/<res>/<index>   Win32 dialog control caption (arbitrary length)
  UTF16/<hex>                 In-place UTF-16LE string in a data section
  CP932/<hex>                 In-place cp932 string in a data section

Resource entries rebuild the .rsrc section cleanly (appending a .rsrc2 section
if the translated resources grow), so English labels are not limited to the
Japanese byte length. Static data-section strings are patched in place within
their original byte length (`max_bytes` / `max_characters` in `units`). Always
build from the original exe under refs/dazedtl/originals, never a patched one.
"""

import argparse
import json
import re
import struct
import sys
from pathlib import Path

RT_MENU = 4
RT_DIALOG = 5
RT_STRING = 6
MO_MAGIC_LE = 0x950412DE
_JAPANESE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]")
_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_HALFWIDTH_KANA = re.compile(r"[\uff61-\uff9f]")
_POINTER_TAIL = re.compile(r"[\u3040-\u9fff][a-z]$")
_CHARSET_RE = re.compile(r"charset=([^\s;]+)", re.IGNORECASE)
_SKIP_SECTIONS = {b".rsrc", b".reloc", b".idata", b".edata", b".pdata", b".tls"}
_SCN_EXEC = 0x20000000
_SCN_INIT_DATA = 0x00000040


class ExeError(ValueError):
    pass


def _u16(data, at):
    if at < 0 or at + 2 > len(data):
        raise ExeError(f"Unexpected end of PE data at {at:#x}.")
    return struct.unpack_from("<H", data, at)[0]


def _u32(data, at):
    if at < 0 or at + 4 > len(data):
        raise ExeError(f"Unexpected end of PE data at {at:#x}.")
    return struct.unpack_from("<I", data, at)[0]


def _align(value, boundary):
    return (value + boundary - 1) & ~(boundary - 1) if boundary else value


def _printable_text(text):
    if not text or not _JAPANESE.search(text):
        return False
    for ch in text:
        code = ord(ch)
        if ch in "\t\r\n":
            continue
        if code < 0x20 or 0x7F <= code <= 0x9F or 0xD800 <= code <= 0xDFFF:
            return False
        if 0xE000 <= code <= 0xF8FF or code >= 0xFFF0:
            return False
    return True


def _is_cp932_clean(text):
    try:
        text.encode("cp932")
    except UnicodeEncodeError:
        return False
    return True


def _read_utf16_z(data, at):
    end = at
    while end + 2 <= len(data):
        if data[end] == 0 and data[end + 1] == 0:
            return data[at:end].decode("utf-16-le"), end + 2
        end += 2
    raise ExeError(f"Unterminated UTF-16 string at {at:#x}.")


def _read_sz_or_ord(data, at):
    first = _u16(data, at)
    if first == 0x0000:
        return ("empty", None), at + 2
    if first == 0xFFFF:
        return ("ord", _u16(data, at + 2)), at + 4
    text, end = _read_utf16_z(data, at)
    return ("str", text), end


def _write_sz_or_ord(item):
    kind, value = item
    if kind == "empty":
        return b"\x00\x00"
    if kind == "ord":
        return struct.pack("<HH", 0xFFFF, value)
    return value.encode("utf-16-le") + b"\x00\x00"


def _res_label(key):
    is_named, value = key
    return value if is_named else str(value)


# --- GNU gettext .mo (SAS5 GMO resource) --------------------------------------


def _hashpjw(data):
    """GNU gettext PJW string hash for .mo hash tables."""
    h = 0
    for byte in data:
        h = (h << 4) + byte
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
            h ^= g
    return h & 0xFFFFFFFF


def _mo_encoding(header_bytes):
    text = header_bytes.decode("ascii", "ignore")
    match = _CHARSET_RE.search(text)
    if not match:
        return "cp932"
    raw = match.group(1).strip().lower()
    if raw in ("shift_jis", "shift-jis", "sjis", "cp932", "ms932", "windows-31j"):
        return "cp932"
    if raw in ("utf-8", "utf8"):
        return "utf-8"
    return raw


def _parse_mo(data):
    """Parse a little-endian GNU gettext .mo catalog."""
    if len(data) < 28:
        return None
    magic, rev, count, orig_off, trans_off, hash_size, hash_off = struct.unpack_from(
        "<7I", data, 0
    )
    if magic != MO_MAGIC_LE:
        return None
    if orig_off + count * 8 > len(data) or trans_off + count * 8 > len(data):
        raise ExeError("Truncated GMO catalog table.")
    origs, trans = [], []
    for i in range(count):
        olen, opos = struct.unpack_from("<II", data, orig_off + i * 8)
        tlen, tpos = struct.unpack_from("<II", data, trans_off + i * 8)
        if opos + olen > len(data) or tpos + tlen > len(data):
            raise ExeError("Truncated GMO string entry.")
        origs.append(data[opos : opos + olen])
        trans.append(data[tpos : tpos + tlen])
    encoding = _mo_encoding(trans[0]) if origs and origs[0] == b"" else "cp932"
    return {
        "rev": rev,
        "hash_size": hash_size,
        "origs": origs,
        "trans": trans,
        "encoding": encoding,
    }


def _build_mo(parsed, replacements):
    """Rebuild a GNU gettext .mo catalog with *replacements* ({index: text})."""
    origs = parsed["origs"]
    encoding = parsed["encoding"]
    trans = list(parsed["trans"])
    for index, text in replacements.items():
        try:
            encoded = text.encode(encoding)
        except UnicodeEncodeError as exc:
            raise ExeError(f"GMO entry {index}: {exc}") from exc
        if b"\x00" in encoded:
            raise ExeError(f"GMO entry {index} contains a NUL byte.")
        trans[index] = encoded
    count = len(origs)
    orig_off = 28
    trans_off = orig_off + count * 8
    hash_size = parsed["hash_size"]
    hash_off = trans_off + count * 8
    strings_off = hash_off + hash_size * 4

    hash_table = [0] * hash_size
    if hash_size > 2:
        for idx, raw in enumerate(origs):
            h = _hashpjw(raw)
            slot = h % hash_size
            incr = 1 + (h % (hash_size - 2))
            while hash_table[slot] != 0:
                slot = (slot + incr) % hash_size
            hash_table[slot] = idx + 1

    orig_table = bytearray()
    trans_table = bytearray()
    pool = bytearray()
    for raw in origs:
        orig_table += struct.pack("<II", len(raw), strings_off + len(pool))
        pool += raw + b"\x00"
    for raw in trans:
        trans_table += struct.pack("<II", len(raw), strings_off + len(pool))
        pool += raw + b"\x00"

    header = struct.pack(
        "<7I",
        MO_MAGIC_LE,
        parsed["rev"],
        count,
        orig_off,
        trans_off,
        hash_size,
        hash_off if hash_size else 0,
    )
    ht_bytes = struct.pack(f"<{hash_size}I", *hash_table) if hash_size else b""
    return header + bytes(orig_table) + bytes(trans_table) + ht_bytes + bytes(pool)


# --- Standard Win32 resources (RT_STRING, RT_MENU, RT_DIALOG) -----------------


def _parse_string_block(data, block_id):
    at, slots = 0, []
    for _slot in range(16):
        if at + 2 > len(data):
            slots.append("")
            continue
        length = _u16(data, at)
        at += 2
        raw = data[at : at + length * 2]
        at += length * 2
        slots.append(raw.decode("utf-16-le", "replace"))
    return slots


def _build_string_block(slots):
    out = bytearray()
    for text in slots:
        encoded = text.encode("utf-16-le")
        out += struct.pack("<H", len(encoded) // 2) + encoded
    return bytes(out)


def _parse_menu(data):
    """Parse standard or extended Win32 RT_MENU into a rebuildable list."""
    if len(data) < 4:
        return None
    version, offset = struct.unpack_from("<HH", data, 0)
    if version == 0:
        at = 4 + offset
        items = []
        depth = 1
        while depth > 0 and at + 2 <= len(data):
            flags = _u16(data, at)
            at += 2
            is_popup = bool(flags & 0x0010)
            item_id = None
            if not is_popup:
                item_id = _u16(data, at)
                at += 2
            caption, at = _read_utf16_z(data, at)
            items.append((flags, item_id, caption))
            if is_popup:
                depth += 1
            if flags & 0x0080:
                depth -= 1
        return ("std", data[: 4 + offset], items, data[at:])
    if version == 1 and offset >= 4 and len(data) >= 4 + offset:
        header = data[: 4 + offset]
        at = 4 + offset
        items = []
        depth = 1
        while depth > 0 and at + 14 <= len(data):
            at = _align(at, 4)
            if at + 14 > len(data):
                break
            mtype, mstate, menu_id, res_info = struct.unpack_from("<IIIH", data, at)
            at += 14
            caption, at = _read_utf16_z(data, at)
            at = _align(at, 4)
            help_id = None
            is_popup = bool(res_info & 0x01)
            if is_popup:
                help_id = _u32(data, at)
                at += 4
                depth += 1
            items.append((mtype, mstate, menu_id, res_info, caption, help_id))
            if res_info & 0x80:
                depth -= 1
        return ("ex", header, items, data[at:])
    return None


def _build_menu(parsed, replacements):
    kind, header, items, tail = parsed
    out = bytearray(header)
    if kind == "std":
        for idx, (flags, item_id, caption) in enumerate(items):
            text = replacements.get(idx, caption)
            out += struct.pack("<H", flags)
            if item_id is not None:
                out += struct.pack("<H", item_id)
            out += text.encode("utf-16-le") + b"\x00\x00"
        out += tail
        return bytes(out)
    for idx, (mtype, mstate, menu_id, res_info, caption, help_id) in enumerate(items):
        while len(out) % 4:
            out.append(0)
        text = replacements.get(idx, caption)
        out += struct.pack("<IIIH", mtype, mstate, menu_id, res_info)
        out += text.encode("utf-16-le") + b"\x00\x00"
        while len(out) % 4:
            out.append(0)
        if help_id is not None:
            out += struct.pack("<I", help_id)
    out += tail
    return bytes(out)


def _parse_dialog(data):
    """Parse standard or extended Win32 RT_DIALOG into a rebuildable structure."""
    if len(data) < 18:
        return None
    ver, sig = struct.unpack_from("<HH", data, 0)
    extended = ver == 1 and sig == 0xFFFF
    if extended:
        if len(data) < 26:
            return None
        style = _u32(data, 12)
        cdit = _u16(data, 16)
        head = data[:26]
        at = 26
    else:
        style = _u32(data, 0)
        cdit = _u16(data, 8)
        head = data[:18]
        at = 18
    try:
        menu, at = _read_sz_or_ord(data, at)
        wclass, at = _read_sz_or_ord(data, at)
        title, at = _read_utf16_z(data, at)
        font = None
        if style & 0x0040:  # DS_SETFONT / DS_SHELLFONT
            if extended:
                font_head = data[at : at + 6]
                at += 6
            else:
                font_head = data[at : at + 2]
                at += 2
            typeface, at = _read_utf16_z(data, at)
            font = (font_head, typeface)
        controls = []
        for _ in range(cdit):
            at = _align(at, 4)
            ctrl_head_size = 24 if extended else 18
            if at + ctrl_head_size > len(data):
                return None
            ctrl_head = data[at : at + ctrl_head_size]
            at += ctrl_head_size
            cclass, at = _read_sz_or_ord(data, at)
            ctitle, at = _read_sz_or_ord(data, at)
            extra_len = _u16(data, at)
            at += 2
            if at + extra_len > len(data):
                return None
            extra = data[at : at + extra_len]
            at += extra_len
            controls.append((ctrl_head, cclass, ctitle, extra))
    except ExeError:
        return None
    return (extended, head, menu, wclass, title, font, controls, data[at:])


def _build_dialog(parsed, title_repl, ctrl_repls):
    _extended, head, menu, wclass, title, font, controls, tail = parsed
    out = bytearray(head)
    out += _write_sz_or_ord(menu)
    out += _write_sz_or_ord(wclass)
    out += (title if title_repl is None else title_repl).encode("utf-16-le") + b"\x00\x00"
    if font is not None:
        out += font[0] + font[1].encode("utf-16-le") + b"\x00\x00"
    for idx, (ctrl_head, cclass, ctitle, extra) in enumerate(controls):
        while len(out) % 4:
            out.append(0)
        out += ctrl_head
        out += _write_sz_or_ord(cclass)
        if idx in ctrl_repls:
            out += _write_sz_or_ord(("str", ctrl_repls[idx]))
        else:
            out += _write_sz_or_ord(ctitle)
        out += struct.pack("<H", len(extra)) + extra
    out += tail
    return bytes(out)


class PeFile:
    """Minimal stdlib-only Windows PE reader and SAS5 UI-string patcher."""

    def __init__(self, raw):
        if len(raw) < 0x40 or raw[:2] != b"MZ":
            raise ExeError("This is not a Windows PE executable.")
        self.raw = raw
        self.pe_off = _u32(raw, 0x3C)
        if raw[self.pe_off : self.pe_off + 4] != b"PE\0\0":
            raise ExeError("Missing PE signature.")
        self.num_sections = _u16(raw, self.pe_off + 6)
        self.opt_size = _u16(raw, self.pe_off + 20)
        self.opt_off = self.pe_off + 24
        self.magic = _u16(raw, self.opt_off)
        if self.magic not in (0x10B, 0x20B):
            raise ExeError(f"Unsupported PE optional header magic {self.magic:#x}.")
        self.sec_align, self.file_align = struct.unpack_from(
            "<II", raw, self.opt_off + 32
        )
        self.size_of_image, self.size_of_headers = struct.unpack_from(
            "<II", raw, self.opt_off + 56
        )
        self.checksum_off = self.opt_off + 64
        rva_count_off = self.opt_off + (92 if self.magic == 0x10B else 108)
        self.dd_off = self.opt_off + (96 if self.magic == 0x10B else 112)
        self.num_rva = _u32(raw, rva_count_off)
        self.rsrc_rva, self.rsrc_size = (
            struct.unpack_from("<II", raw, self.dd_off + 16)
            if self.num_rva > 2
            else (0, 0)
        )
        self.sec_table_off = self.opt_off + self.opt_size
        self.sections = []
        for idx in range(self.num_sections):
            off = self.sec_table_off + idx * 40
            name = raw[off : off + 8].rstrip(b"\x00")
            vsize, vaddr, raw_size, raw_ptr = struct.unpack_from("<4I", raw, off + 8)
            chars = _u32(raw, off + 36)
            self.sections.append(
                {
                    "index": idx,
                    "header_off": off,
                    "name": name,
                    "vsize": vsize,
                    "vaddr": vaddr,
                    "raw_size": raw_size,
                    "raw_ptr": raw_ptr,
                    "chars": chars,
                }
            )
        self.resources = self._read_resources()

    def rva_to_offset(self, rva):
        for sec in self.sections:
            span = max(sec["vsize"], sec["raw_size"])
            if sec["vaddr"] <= rva < sec["vaddr"] + span:
                off = sec["raw_ptr"] + (rva - sec["vaddr"])
                if off <= len(self.raw):
                    return off
        return None

    def _rsrc_section(self):
        if not self.rsrc_rva:
            return None
        for sec in self.sections:
            span = max(sec["vsize"], sec["raw_size"])
            if sec["vaddr"] <= self.rsrc_rva < sec["vaddr"] + span:
                return sec
        return None

    def _read_resources(self):
        """Return {type_key: {name_key: {lang_key: (data, codepage, reserved)}}}."""
        if not self.rsrc_rva or not self.rsrc_size:
            return {}
        base_off = self.rva_to_offset(self.rsrc_rva)
        if base_off is None:
            return {}
        rsrc = self.raw[base_off : base_off + self.rsrc_size]
        tree = {}

        def read_dir(dir_off, level, path):
            if dir_off + 16 > len(rsrc):
                return
            named_count, id_count = struct.unpack_from("<HH", rsrc, dir_off + 12)
            entry_off = dir_off + 16
            for _ in range(named_count + id_count):
                if entry_off + 8 > len(rsrc):
                    break
                name_or_id, offset = struct.unpack_from("<II", rsrc, entry_off)
                entry_off += 8
                if name_or_id & 0x80000000:
                    str_off = name_or_id & 0x7FFFFFFF
                    if str_off + 2 > len(rsrc):
                        continue
                    slen = _u16(rsrc, str_off)
                    key = (
                        True,
                        rsrc[str_off + 2 : str_off + 2 + slen * 2].decode(
                            "utf-16-le", "replace"
                        ),
                    )
                else:
                    key = (False, name_or_id)
                if offset & 0x80000000:
                    if level < 2:
                        read_dir(offset & 0x7FFFFFFF, level + 1, (*path, key))
                elif level == 2:
                    if offset + 16 > len(rsrc):
                        continue
                    data_rva, data_size, cp, reserved = struct.unpack_from(
                        "<4I", rsrc, offset
                    )
                    data_off = self.rva_to_offset(data_rva)
                    if data_off is None or data_off + data_size > len(self.raw):
                        continue
                    tkey, nkey = path
                    tree.setdefault(tkey, {}).setdefault(nkey, {})[key] = (
                        self.raw[data_off : data_off + data_size],
                        cp,
                        reserved,
                    )

        read_dir(0, 0, ())
        return tree

    def _gmo_type_keys(self):
        return [
            tkey
            for tkey in sorted(self.resources)
            if tkey[0] and tkey[1].upper() == "GMO"
        ]

    def _resource_units(self):
        found = []
        # SAS5 GMO (GNU gettext .mo UI catalog)
        for tkey in self._gmo_type_keys():
            for nkey, langs in sorted(self.resources[tkey].items()):
                rlabel = _res_label(nkey)
                for lkey, (blob, _cp, _res) in sorted(langs.items()):
                    suffix = f"/{_res_label(lkey)}" if len(langs) > 1 else ""
                    parsed = _parse_mo(blob)
                    if not parsed:
                        continue
                    enc = parsed["encoding"]
                    for idx, (orig, trans) in enumerate(
                        zip(parsed["origs"], parsed["trans"], strict=True)
                    ):
                        if idx == 0 and orig == b"":
                            continue
                        text = trans.decode(enc, "replace")
                        if _printable_text(text):
                            found.append(
                                {
                                    "id": f"RSRC/GMO/{rlabel}/{idx}{suffix}",
                                    "kind": "rsrc_gmo",
                                    "encoding": enc,
                                    "msgid": orig.decode(enc, "replace"),
                                    "text": text,
                                }
                            )
        # RT_STRING
        for nkey, langs in sorted(self.resources.get((False, RT_STRING), {}).items()):
            if nkey[0]:
                continue
            block_id = nkey[1]
            for lkey, (blob, _cp, _res) in sorted(langs.items()):
                suffix = f"/{_res_label(lkey)}" if len(langs) > 1 else ""
                for slot, text in enumerate(_parse_string_block(blob, block_id)):
                    if _printable_text(text):
                        sid = (block_id - 1) * 16 + slot
                        found.append(
                            {
                                "id": f"RSRC/STRING/{sid}{suffix}",
                                "kind": "rsrc_string",
                                "encoding": "utf-16-le",
                                "text": text,
                            }
                        )
        # RT_MENU
        for nkey, langs in sorted(self.resources.get((False, RT_MENU), {}).items()):
            rlabel = _res_label(nkey)
            for lkey, (blob, _cp, _res) in sorted(langs.items()):
                suffix = f"/{_res_label(lkey)}" if len(langs) > 1 else ""
                parsed = _parse_menu(blob)
                if not parsed:
                    continue
                items = parsed[2]
                for idx, item in enumerate(items):
                    caption = item[2] if parsed[0] == "std" else item[4]
                    if _printable_text(caption):
                        found.append(
                            {
                                "id": f"RSRC/MENU/{rlabel}/{idx}{suffix}",
                                "kind": "rsrc_menu",
                                "encoding": "utf-16-le",
                                "text": caption,
                            }
                        )
        # RT_DIALOG
        for nkey, langs in sorted(self.resources.get((False, RT_DIALOG), {}).items()):
            rlabel = _res_label(nkey)
            for lkey, (blob, _cp, _res) in sorted(langs.items()):
                suffix = f"/{_res_label(lkey)}" if len(langs) > 1 else ""
                parsed = _parse_dialog(blob)
                if not parsed:
                    continue
                _, _, _, _, title, _, controls, _ = parsed
                if _printable_text(title):
                    found.append(
                        {
                            "id": f"RSRC/DIALOG/{rlabel}/title{suffix}",
                            "kind": "rsrc_dialog",
                            "encoding": "utf-16-le",
                            "text": title,
                        }
                    )
                for idx, (_head, _cls, ctitle, _extra) in enumerate(controls):
                    if ctitle[0] == "str" and _printable_text(ctitle[1]):
                        found.append(
                            {
                                "id": f"RSRC/DIALOG/{rlabel}/{idx}{suffix}",
                                "kind": "rsrc_dialog",
                                "encoding": "utf-16-le",
                                "text": ctitle[1],
                            }
                        )
        return found

    def _data_units(self):
        found = []
        used_ranges = []
        for sec in self.sections:
            if (
                sec["name"] in _SKIP_SECTIONS
                or sec["chars"] & _SCN_EXEC
                or not (sec["chars"] & _SCN_INIT_DATA)
                or sec["raw_size"] == 0
            ):
                continue
            base = sec["raw_ptr"]
            blob = self.raw[base : base + sec["raw_size"]]
            sec_name = sec["name"].decode("ascii", "replace")
            # 1. NUL-terminated UTF-16LE strings on 2-byte boundaries preceded
            #    by NUL (or section start), filtering out ASCII-byte pairs and
            #    32-bit pointer tables misread as CJK codepoints.
            at = 0
            limit = len(blob) - 1
            while at < limit:
                if at > 0 and blob[at - 1] != 0:
                    at += 2
                    continue
                end = at
                chars = []
                valid = True
                while end + 2 <= len(blob):
                    code = blob[end] | (blob[end + 1] << 8)
                    if code == 0:
                        break
                    if not (
                        0x20 <= code <= 0x7E
                        or code in (9, 10, 13)
                        or 0x3000 <= code <= 0x30FF
                        or 0x4E00 <= code <= 0x9FFF
                        or 0xFF01 <= code <= 0xFF5E
                    ):
                        valid = False
                        break
                    chars.append(chr(code))
                    end += 2
                if (
                    valid
                    and len(chars) >= 2
                    and end + 2 <= len(blob)
                    and blob[end] == 0
                    and blob[end + 1] == 0
                ):
                    raw_bytes = blob[at:end]
                    text = "".join(chars)
                    all_ascii_bytes = all(
                        0x20 <= b <= 0x7E or b in (9, 10, 13) for b in raw_bytes
                    )
                    if (
                        not all_ascii_bytes
                        and _printable_text(text)
                        and _is_cp932_clean(text)
                        and _KANA.search(text)
                        and not (len(chars) <= 5 and _POINTER_TAIL.search(text))
                    ):
                        byte_len = end - at
                        offset = base + at
                        used_ranges.append((offset, offset + byte_len + 2))
                        found.append(
                            {
                                "id": f"UTF16/{offset:06x}",
                                "kind": "utf16",
                                "section": sec_name,
                                "encoding": "utf-16-le",
                                "max_bytes": byte_len,
                                "max_characters": byte_len // 2,
                                "text": text,
                            }
                        )
                        at = end + 2
                        continue
                at += 2

            # 2. NUL-terminated cp932 strings not overlapping UTF-16LE hits.
            def overlaps(start, stop):
                return any(a < stop and start < b for a, b in used_ranges)

            at = 0
            while at < len(blob):
                zero = blob.find(b"\x00", at)
                if zero < 0:
                    break
                if zero - at >= 4:
                    chunk = blob[at:zero]
                    if any(b >= 0x81 for b in chunk) and not overlaps(
                        base + at, base + zero + 1
                    ):
                        try:
                            text = chunk.decode("cp932")
                        except UnicodeDecodeError:
                            text = ""
                        if (
                            _printable_text(text)
                            and len(text) >= 4
                            and not _HALFWIDTH_KANA.search(text)
                            and _KANA.search(text)
                            and sum(
                                bool(_JAPANESE.match(ch))
                                or ch.isalnum()
                                or ch in " 。、！？!?()（）:：・'-/\\\n\t"
                                for ch in text
                            )
                            >= len(text) * 0.85
                        ):
                            offset = base + at
                            found.append(
                                {
                                    "id": f"CP932/{offset:06x}",
                                    "kind": "cp932",
                                    "section": sec_name,
                                    "encoding": "cp932",
                                    "max_bytes": zero - at,
                                    "max_characters": zero - at,
                                    "text": text,
                                }
                            )
                at = zero + 1
        return found

    def units(self):
        return self._resource_units() + self._data_units()

    def _serialize_resources(self, tree, base_rva):
        """Serialize a 3-level Win32 resource tree rooted at *base_rva*."""

        def split_entries(mapping):
            named = sorted(
                ((k, v) for k, v in mapping.items() if k[0]), key=lambda kv: kv[0][1]
            )
            by_id = sorted(
                ((k, v) for k, v in mapping.items() if not k[0]),
                key=lambda kv: kv[0][1],
            )
            return named, by_id

        # Pass 1: measure directory and leaf entry sizes.
        dir_offsets = {}
        cursor = 0

        def reserve_dir(node_id, mapping):
            nonlocal cursor
            dir_offsets[node_id] = cursor
            named, by_id = split_entries(mapping)
            cursor += 16 + 8 * (len(named) + len(by_id))
            return named, by_id

        t_named, t_ids = reserve_dir(("root",), tree)
        name_dirs = []
        for tkey, nmap in [*t_named, *t_ids]:
            n_named, n_ids = reserve_dir(("type", tkey), nmap)
            name_dirs.append((tkey, n_named, n_ids))
        lang_dirs = []
        for tkey, n_named, n_ids in name_dirs:
            for nkey, lmap in [*n_named, *n_ids]:
                l_named, l_ids = reserve_dir(("name", tkey, nkey), lmap)
                lang_dirs.append((tkey, nkey, l_named, l_ids))

        # Resource data entries (16 bytes each).
        leaf_offsets = {}
        leaves = []
        for tkey, nkey, l_named, l_ids in lang_dirs:
            for lkey, payload in [*l_named, *l_ids]:
                leaf_offsets[(tkey, nkey, lkey)] = cursor
                cursor += 16
                leaves.append((tkey, nkey, lkey, payload))

        # String names pool (2-byte aligned: uint16 length + UTF-16LE).
        string_offsets = {}
        string_pool = bytearray()
        cursor = _align(cursor, 2)
        string_base = cursor
        all_names = []
        for tkey in tree:
            if tkey[0]:
                all_names.append(tkey[1])
            for nkey in tree[tkey]:
                if nkey[0]:
                    all_names.append(nkey[1])
                for lkey in tree[tkey][nkey]:
                    if lkey[0]:
                        all_names.append(lkey[1])
        for name in sorted(set(all_names)):
            string_offsets[name] = string_base + len(string_pool)
            encoded = name.encode("utf-16-le")
            string_pool += struct.pack("<H", len(encoded) // 2) + encoded

        data_start = _align(string_base + len(string_pool), 4)
        payload_offsets = {}
        payload_blob = bytearray()
        for tkey, nkey, lkey, (data, _cp, _res) in leaves:
            while (data_start + len(payload_blob)) % 4:
                payload_blob.append(0)
            payload_offsets[(tkey, nkey, lkey)] = data_start + len(payload_blob)
            payload_blob += data

        # Pass 2: emit directories, leaf entries, strings and payloads.
        out = bytearray()

        def emit_dir(mapping, child_fn):
            named, by_id = split_entries(mapping)
            out.extend(struct.pack("<IIHHHH", 0, 0, 0, 0, len(named), len(by_id)))
            for key, _val in named:
                out.extend(
                    struct.pack(
                        "<II", 0x80000000 | string_offsets[key[1]], child_fn(key)
                    )
                )
            for key, _val in by_id:
                out.extend(struct.pack("<II", key[1], child_fn(key)))

        emit_dir(tree, lambda tk: 0x80000000 | dir_offsets[("type", tk)])
        for tkey, n_named, n_ids in name_dirs:
            emit_dir(
                tree[tkey],
                lambda nk, tk=tkey: 0x80000000 | dir_offsets[("name", tk, nk)],
            )
        for tkey, nkey, l_named, l_ids in lang_dirs:
            emit_dir(
                tree[tkey][nkey],
                lambda lk, tk=tkey, nk=nkey: leaf_offsets[(tk, nk, lk)],
            )
        for tkey, nkey, lkey, (data, cp, res) in leaves:
            out.extend(
                struct.pack(
                    "<4I",
                    base_rva + payload_offsets[(tkey, nkey, lkey)],
                    len(data),
                    cp,
                    res,
                )
            )
        while len(out) < string_base:
            out.append(0)
        out.extend(string_pool)
        while len(out) < data_start:
            out.append(0)
        out.extend(payload_blob)
        return bytes(out)

    def check(self):
        """Verify GMO catalogs and Win32 resources round-trip identically."""
        problems = []
        for tkey in self._gmo_type_keys():
            for nkey, langs in sorted(self.resources[tkey].items()):
                for lkey, (blob, _cp, _res) in sorted(langs.items()):
                    parsed = _parse_mo(blob)
                    if not parsed or _build_mo(parsed, {}) != blob:
                        problems.append(
                            f"GMO/{_res_label(nkey)}/{_res_label(lkey)} did not round-trip"
                        )
        for nkey, langs in sorted(self.resources.get((False, RT_MENU), {}).items()):
            for lkey, (blob, _cp, _res) in sorted(langs.items()):
                parsed = _parse_menu(blob)
                if not parsed or _build_menu(parsed, {}) != blob:
                    problems.append(
                        f"RT_MENU/{_res_label(nkey)}/{_res_label(lkey)} did not round-trip"
                    )
        for nkey, langs in sorted(self.resources.get((False, RT_DIALOG), {}).items()):
            for lkey, (blob, _cp, _res) in sorted(langs.items()):
                parsed = _parse_dialog(blob)
                if not parsed or _build_dialog(parsed, None, {}) != blob:
                    problems.append(
                        f"RT_DIALOG/{_res_label(nkey)}/{_res_label(lkey)} did not round-trip"
                    )
        return problems

    def rebuild(self, translations):
        """Return PE bytes with *translations* applied to resources and data strings."""
        units = {unit["id"]: unit for unit in self.units()}
        out = bytearray(self.raw)
        rsrc_edits = {}
        for unit_id, text in translations.items():
            if unit_id not in units:
                raise ExeError(
                    f"No unit {unit_id} in this executable. Build from the original "
                    ".exe your units came from, which the game's Git keeps under "
                    "refs/dazedtl/originals, never from a patched one."
                )
            unit = units[unit_id]
            kind = unit["kind"]
            if kind in ("utf16", "cp932"):
                offset = int(unit_id.split("/", 1)[1], 16)
                max_bytes = unit["max_bytes"]
                try:
                    encoded = text.encode(unit["encoding"])
                except UnicodeEncodeError as exc:
                    raise ExeError(f"{unit_id}: {exc}") from exc
                if len(encoded) > max_bytes:
                    raise ExeError(
                        f"{unit_id} takes {len(encoded)} bytes in {unit['encoding']}; "
                        f"{max_bytes} fit in place."
                    )
                out[offset : offset + max_bytes] = encoded + bytes(
                    max_bytes - len(encoded)
                )
            else:
                rsrc_edits[unit_id] = text

        if rsrc_edits:
            tree = {
                tk: {nk: dict(lmap) for nk, lmap in nmap.items()}
                for tk, nmap in self.resources.items()
            }
            # Apply GMO edits.
            for tkey in self._gmo_type_keys():
                for nkey, langs in tree.get(tkey, {}).items():
                    rlabel = _res_label(nkey)
                    for lkey in list(langs):
                        suffix = f"/{_res_label(lkey)}" if len(langs) > 1 else ""
                        blob, cp, res = langs[lkey]
                        parsed = _parse_mo(blob)
                        if not parsed:
                            continue
                        repls = {}
                        for idx in range(len(parsed["origs"])):
                            uid = f"RSRC/GMO/{rlabel}/{idx}{suffix}"
                            if uid in rsrc_edits:
                                repls[idx] = rsrc_edits[uid]
                        if repls:
                            langs[lkey] = (_build_mo(parsed, repls), cp, res)
            # Apply RT_STRING edits.
            for nkey, langs in tree.get((False, RT_STRING), {}).items():
                if nkey[0]:
                    continue
                block_id = nkey[1]
                for lkey in list(langs):
                    suffix = f"/{_res_label(lkey)}" if len(langs) > 1 else ""
                    blob, cp, res = langs[lkey]
                    slots = _parse_string_block(blob, block_id)
                    changed = False
                    for slot in range(16):
                        uid = f"RSRC/STRING/{(block_id - 1) * 16 + slot}{suffix}"
                        if uid in rsrc_edits:
                            slots[slot] = rsrc_edits[uid]
                            changed = True
                    if changed:
                        langs[lkey] = (_build_string_block(slots), cp, res)
            # Apply RT_MENU edits.
            for nkey, langs in tree.get((False, RT_MENU), {}).items():
                rlabel = _res_label(nkey)
                for lkey in list(langs):
                    suffix = f"/{_res_label(lkey)}" if len(langs) > 1 else ""
                    blob, cp, res = langs[lkey]
                    parsed = _parse_menu(blob)
                    if not parsed:
                        continue
                    repls = {}
                    for idx in range(len(parsed[2])):
                        uid = f"RSRC/MENU/{rlabel}/{idx}{suffix}"
                        if uid in rsrc_edits:
                            repls[idx] = rsrc_edits[uid]
                    if repls:
                        langs[lkey] = (_build_menu(parsed, repls), cp, res)
            # Apply RT_DIALOG edits.
            for nkey, langs in tree.get((False, RT_DIALOG), {}).items():
                rlabel = _res_label(nkey)
                for lkey in list(langs):
                    suffix = f"/{_res_label(lkey)}" if len(langs) > 1 else ""
                    blob, cp, res = langs[lkey]
                    parsed = _parse_dialog(blob)
                    if not parsed:
                        continue
                    title_uid = f"RSRC/DIALOG/{rlabel}/title{suffix}"
                    title_repl = rsrc_edits.get(title_uid)
                    ctrl_repls = {}
                    for idx in range(len(parsed[6])):
                        uid = f"RSRC/DIALOG/{rlabel}/{idx}{suffix}"
                        if uid in rsrc_edits:
                            ctrl_repls[idx] = rsrc_edits[uid]
                    if title_repl is not None or ctrl_repls:
                        langs[lkey] = (
                            _build_dialog(parsed, title_repl, ctrl_repls),
                            cp,
                            res,
                        )

            rsrc_sec = self._rsrc_section()
            if rsrc_sec is None:
                raise ExeError("The executable has no .rsrc section.")
            in_place = self._serialize_resources(tree, rsrc_sec["vaddr"])
            if len(in_place) <= rsrc_sec["raw_size"]:
                start = rsrc_sec["raw_ptr"]
                out[start : start + rsrc_sec["raw_size"]] = in_place + bytes(
                    rsrc_sec["raw_size"] - len(in_place)
                )
                struct.pack_into("<I", out, rsrc_sec["header_off"] + 8, len(in_place))
                struct.pack_into(
                    "<II", out, self.dd_off + 16, rsrc_sec["vaddr"], len(in_place)
                )
            else:
                new_hdr_off = self.sec_table_off + self.num_sections * 40
                first_raw = min(
                    (s["raw_ptr"] for s in self.sections if s["raw_ptr"]),
                    default=self.size_of_headers,
                )
                if new_hdr_off + 40 > first_raw:
                    raise ExeError(
                        "No room in the PE header table to append an expanded .rsrc section."
                    )
                new_vaddr = max(
                    _align(s["vaddr"] + max(s["vsize"], s["raw_size"]), self.sec_align)
                    for s in self.sections
                )
                new_raw_ptr = _align(len(out), self.file_align)
                new_blob = self._serialize_resources(tree, new_vaddr)
                new_raw_size = _align(len(new_blob), self.file_align)
                out.extend(bytes(new_raw_ptr - len(out)))
                out.extend(new_blob)
                out.extend(bytes(new_raw_size - len(new_blob)))
                hdr = struct.pack(
                    "<8sIIIIIIHHI",
                    b".rsrc2\x00\x00",
                    len(new_blob),
                    new_vaddr,
                    new_raw_size,
                    new_raw_ptr,
                    0,
                    0,
                    0,
                    0,
                    0x40000040,  # IMAGE_SCN_CNT_INITIALIZED_DATA | IMAGE_SCN_MEM_READ
                )
                out[new_hdr_off : new_hdr_off + 40] = hdr
                struct.pack_into("<H", out, self.pe_off + 6, self.num_sections + 1)
                new_image_size = _align(new_vaddr + len(new_blob), self.sec_align)
                struct.pack_into("<I", out, self.opt_off + 56, new_image_size)
                struct.pack_into(
                    "<II", out, self.dd_off + 16, new_vaddr, len(new_blob)
                )
            # Clear optional PE checksum so Windows accepts the rebuilt binary.
            struct.pack_into("<I", out, self.checksum_off, 0)
        return bytes(out)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, *extra in (
        ("info",),
        ("check",),
        ("decode", "out"),
        ("units", "out"),
        ("patch", "translations", "out"),
    ):
        command = commands.add_parser(name)
        for argument in ("exe", *extra):
            command.add_argument(argument, type=Path)
    args = parser.parse_args()
    pe = PeFile(args.exe.read_bytes())
    units = pe.units()
    if args.command == "info":
        counts = {}
        for unit in units:
            counts[unit["kind"]] = counts.get(unit["kind"], 0) + 1
        print(
            f"PE {'64-bit' if pe.magic == 0x20B else '32-bit'}, "
            f"{len(pe.sections)} sections, {len(units)} Japanese UI strings"
        )
        for kind, count in sorted(counts.items()):
            print(f"  {kind}: {count}")
    elif args.command == "check":
        problems = pe.check()
        for problem in problems:
            print("MISMATCH", problem)
        if problems:
            return 1
        print(f"ok: {len(units)} UI strings, resource identity rebuild: same")
        return 0
    elif args.command == "decode":
        args.out.mkdir(parents=True, exist_ok=True)
        groups = {}
        for unit in units:
            bucket = (
                "exe_rsrc"
                if unit["kind"].startswith("rsrc_")
                else f"exe_{unit['kind']}"
            )
            groups.setdefault(bucket, []).append(unit["text"])
        for name, texts in groups.items():
            path = args.out / f"{name}.txt"
            path.write_text("\n".join(texts) + "\n", encoding="utf-8", newline="\n")
    elif args.command == "units":
        text = json.dumps(units, ensure_ascii=False, indent=1)
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        translations = json.loads(args.translations.read_text("utf-8-sig"))
        args.out.write_bytes(pe.rebuild(translations))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ExeError as exc:
        sys.exit(str(exc))

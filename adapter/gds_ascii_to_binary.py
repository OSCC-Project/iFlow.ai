"""iEDA 通用 def_to_gds 的输出是 ASCII GDS (GDT 风格, 首行 "HEADER 0"),
magic/KLayout 等工具只认二进制 GDSII → 行级状态机转换器"""

import struct

# 记录类型: {关键字: (record_type, data_type)}
_REC = {
    "HEADER": (0x00, 0x00), "BGNLIB": (0x01, 0x02), "LIBNAME": (0x02, 0x06),
    "UNITS": (0x03, 0x05), "ENDLIB": (0x04, 0x00), "BGNSTR": (0x05, 0x02),
    "STRNAME": (0x06, 0x06), "ENDSTR": (0x07, 0x00), "BOUNDARY": (0x08, 0x00),
    "PATH": (0x09, 0x00), "SREF": (0x0A, 0x00), "TEXT": (0x0C, 0x00),
    "LAYER": (0x0D, 0x00), "DATATYPE": (0x0E, 0x00), "WIDTH": (0x0F, 0x03),
    "XY": (0x10, 0x03), "ENDEL": (0x11, 0x00), "SNAME": (0x12, 0x06),
    "TEXTTYPE": (0x16, 0x00), "PRESENTATION": (0x17, 0x01), "STRING": (0x19, 0x06),
    "STRANS": (0x1A, 0x01), "MAG": (0x1B, 0x05), "ANGLE": (0x1C, 0x05),
    "PATHTYPE": (0x21, 0x00), "BOX": (0x2D, 0x00), "BOXTYPE": (0x2E, 0x00),
}


def _rec(typ: int, dt: int, payload: bytes) -> bytes:
    length = 4 + len(payload)
    if dt not in (0x03, 0x05) and length % 2:
        payload += b"\x00"
        length += 1
    return struct.pack(">HH", length, (typ << 8) | dt) + payload


def convert(ascii_gds: str) -> bytes:
    """ASCII GDS 文本 → 二进制 GDSII"""
    out = bytearray()
    for raw in ascii_gds.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        key = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        if key not in _REC:
            continue  # 未知记录行跳过 (宽容处理)
        typ, dt = _REC[key]
        if key in ("BGNLIB", "BGNSTR"):
            payload = b"\x00" * 24  # 两个 12 字节时间戳, 归零即可
        elif key == "HEADER":
            payload = struct.pack(">H", int(rest or 0))
        elif key == "UNITS":
            a, b = rest.split()
            payload = struct.pack(">dd", float(a), float(b))
        elif key == "XY":
            # 形如 "20140: 115000" (x: y)
            x, y = rest.split(":")
            payload = struct.pack(">ii", int(float(x)), int(float(y)))
        elif key in ("LAYER", "DATATYPE", "TEXTTYPE", "BOXTYPE", "STRANS", "PRESENTATION", "PATHTYPE"):
            payload = struct.pack(">H", int(rest or 0))
        elif key == "WIDTH":
            payload = struct.pack(">i", int(float(rest or 0)))
        elif key in ("MAG", "ANGLE"):
            payload = struct.pack(">d", float(rest or 0))
        elif key in ("ENDSTR", "ENDEL", "ENDLIB", "BOUNDARY", "PATH", "SREF", "TEXT", "BOX"):
            payload = b""
        else:  # 字符串记录: LIBNAME/STRNAME/SNAME/STRING
            payload = rest.encode("latin-1")
        out += _rec(typ, dt, payload)
    return bytes(out)

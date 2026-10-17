"""TLV family: configurable generic TLV/LTV/LV and BER-TLV (ASN.1 DER/BER, EMV)."""

from __future__ import annotations

import base64
import re
from datetime import datetime

from ..model import ERROR, GROUP, VALUE, Node
from ..reader import DecodeError, Reader, bytes_value, hexs, quote
from .base import ENDIAN, Decoder, Option, new_root, register, run_stream

TEXT_ENCODINGS = [("utf-8", "UTF-8"), ("cp949", "CP949/EUC-KR"), ("latin-1", "Latin-1"),
                  ("utf-16-le", "UTF-16LE"), ("utf-16-be", "UTF-16BE"), ("shift_jis", "Shift-JIS")]


# --------------------------------------------------------------------------
# Generic TLV
# --------------------------------------------------------------------------

def _read_len(r: Reader, size, endian: str) -> int:
    if size == "ber":
        b = r.u8()
        if b < 0x80:
            return b
        n = b & 0x7F
        if n == 0 or n > 4:
            raise DecodeError("BER 길이 형식 오류", r.pos - 1)
        return r.uint(n, "big")
    if size == "varint":
        return r.varint()
    return r.uint(int(size), endian)


def _generic_value(data: bytes, start: int, end: int, opts: dict, depth: int) -> list[Node] | None:
    """Try to parse a value block as nested TLV; only accept a perfect fit."""
    if not opts["nested"] or depth >= opts["max_depth"] or end - start < 2:
        return None
    holder = Node(start, end - start, GROUP, "")
    pos = start
    try:
        while pos < end:
            pos = _generic_item(data, pos, end, holder, opts, depth + 1)
    except DecodeError:
        return None
    if pos != end or any(c.kind == ERROR for c in holder.walk()):
        return None
    return holder.children


def _generic_item(data: bytes, pos: int, end: int, parent: Node, opts: dict, depth: int = 0) -> int:
    r = Reader(data, pos, end)
    endian = opts["endian"]
    layout = opts["layout"]
    tag = None
    length = None
    if layout == "tlv":
        tag = r.uint(int(opts["tag_size"]), endian)
        length = _read_len(r, opts["len_size"], endian)
    elif layout == "ltv":
        length = _read_len(r, opts["len_size"], endian)
        tag = r.uint(int(opts["tag_size"]), endian)
    else:  # lv
        length = _read_len(r, opts["len_size"], endian)
    header = r.pos - pos
    if opts["len_mode"] == "all":
        length -= header
        if length < 0:
            raise DecodeError("길이 값이 헤더보다 작습니다", pos)
    vstart = r.pos
    r.need(length)
    vend = vstart + length
    width = int(opts["tag_size"]) * 2
    label = f"태그 0x{tag:0{width}X}" if tag is not None else "LV"
    nested = _generic_value(data, vstart, vend, opts, depth)
    if nested is not None:
        g = parent.add(Node(pos, vend - pos, GROUP, label, f"(길이 {length})", close="}"))
        g.children.extend(nested)
    else:
        val = bytes_value(data[vstart:vend], (opts["encoding"],))
        if 1 <= length <= 8 and not val.startswith('"'):
            n = int.from_bytes(data[vstart:vend], endian)
            val += f"  (= {n})"
        parent.add(Node(pos, vend - pos, VALUE, label, val))
    return vend


def decode_generic(data: bytes, opts: dict) -> Node:
    root = new_root("TLV", data)
    return run_stream(root, data, lambda p, parent: _generic_item(data, p, len(data), parent, opts))


register(Decoder(
    "tlv", "범용 TLV (태그/길이 크기 지정)", "TLV",
    "태그·길이 필드 크기와 엔디언을 지정하는 단순 TLV/LTV/LV. 값이 다시 TLV로 완전히 해석되면 중첩으로 표시.",
    decode_generic,
    [
        Option("layout", "배치", "choice", "tlv",
               [("tlv", "Tag-Length-Value"), ("ltv", "Length-Tag-Value"), ("lv", "Length-Value (태그 없음)")]),
        Option("tag_size", "태그 크기(byte)", "choice", 1, [(1, "1"), (2, "2"), (4, "4")]),
        Option("len_size", "길이 필드", "choice", 1,
               [(1, "1 byte"), (2, "2 byte"), (4, "4 byte"), ("ber", "BER 길이"), ("varint", "varint(LEB128)")]),
        ENDIAN,
        Option("len_mode", "길이 의미", "choice", "value",
               [("value", "값 길이만"), ("all", "헤더 포함 전체 길이")]),
        Option("nested", "중첩 TLV 추정", "bool", True),
        Option("max_depth", "최대 중첩 깊이", "int", 8, minimum=0, maximum=64),
        Option("encoding", "문자열 인코딩", "choice", "utf-8", TEXT_ENCODINGS),
    ],
    variants=[
        {"tag_size": 1, "len_size": 2},
        {"tag_size": 2, "len_size": 2},
        {"tag_size": 1, "len_size": 2, "endian": "little"},
        {"tag_size": 2, "len_size": 2, "endian": "little"},
        {"tag_size": 2, "len_size": 4, "endian": "little"},
        {"layout": "lv", "len_size": 2},
        {"layout": "lv", "len_size": 4, "endian": "little"},
    ],
))


# --------------------------------------------------------------------------
# BER-TLV (ASN.1 / EMV)
# --------------------------------------------------------------------------

UNIVERSAL = {
    0: "EOC", 1: "BOOLEAN", 2: "INTEGER", 3: "BIT STRING", 4: "OCTET STRING", 5: "NULL",
    6: "OBJECT IDENTIFIER", 7: "ObjectDescriptor", 8: "EXTERNAL", 9: "REAL", 10: "ENUMERATED",
    11: "EMBEDDED PDV", 12: "UTF8String", 13: "RELATIVE-OID", 16: "SEQUENCE", 17: "SET",
    18: "NumericString", 19: "PrintableString", 20: "T61String", 21: "VideotexString",
    22: "IA5String", 23: "UTCTime", 24: "GeneralizedTime", 25: "GraphicString",
    26: "VisibleString", 27: "GeneralString", 28: "UniversalString", 30: "BMPString",
}

OID_NAMES = {
    "2.5.4.3": "commonName", "2.5.4.6": "countryName", "2.5.4.7": "localityName",
    "2.5.4.8": "stateOrProvinceName", "2.5.4.10": "organizationName",
    "2.5.4.11": "organizationalUnitName", "2.5.4.5": "serialNumber",
    "1.2.840.113549.1.1.1": "rsaEncryption", "1.2.840.113549.1.1.5": "sha1WithRSAEncryption",
    "1.2.840.113549.1.1.11": "sha256WithRSAEncryption", "1.2.840.113549.1.1.12": "sha384WithRSAEncryption",
    "1.2.840.113549.1.1.13": "sha512WithRSAEncryption", "1.2.840.113549.1.9.1": "emailAddress",
    "1.2.840.113549.1.7.1": "pkcs7-data", "1.2.840.113549.1.7.2": "pkcs7-signedData",
    "1.2.840.10045.2.1": "ecPublicKey", "1.2.840.10045.3.1.7": "prime256v1",
    "1.3.132.0.34": "secp384r1", "1.2.840.10045.4.3.2": "ecdsa-with-SHA256",
    "1.2.840.10045.4.3.3": "ecdsa-with-SHA384", "1.3.101.112": "Ed25519",
    "2.16.840.1.101.3.4.2.1": "sha256", "2.16.840.1.101.3.4.2.2": "sha384",
    "2.16.840.1.101.3.4.2.3": "sha512", "1.3.14.3.2.26": "sha1",
    "2.5.29.14": "subjectKeyIdentifier", "2.5.29.15": "keyUsage", "2.5.29.17": "subjectAltName",
    "2.5.29.19": "basicConstraints", "2.5.29.31": "cRLDistributionPoints",
    "2.5.29.32": "certificatePolicies", "2.5.29.35": "authorityKeyIdentifier",
    "2.5.29.37": "extKeyUsage", "1.3.6.1.5.5.7.1.1": "authorityInfoAccess",
    "1.3.6.1.5.5.7.3.1": "serverAuth", "1.3.6.1.5.5.7.3.2": "clientAuth",
    "1.3.6.1.5.5.7.48.1": "ocsp", "1.3.6.1.5.5.7.48.2": "caIssuers",
}

EMV_TAGS = {
    "42": "Issuer Identification Number", "4F": "Application Identifier (AID)",
    "50": "Application Label", "57": "Track 2 Equivalent Data", "5A": "Application PAN",
    "5F20": "Cardholder Name", "5F24": "Application Expiration Date", "5F25": "Application Effective Date",
    "5F28": "Issuer Country Code", "5F2A": "Transaction Currency Code", "5F2D": "Language Preference",
    "5F30": "Service Code", "5F34": "PAN Sequence Number", "61": "Application Template",
    "6F": "FCI Template", "70": "READ RECORD Response Template", "71": "Issuer Script Template 1",
    "72": "Issuer Script Template 2", "77": "Response Message Template Format 2",
    "80": "Response Message Template Format 1", "82": "Application Interchange Profile",
    "84": "Dedicated File (DF) Name", "87": "Application Priority Indicator",
    "88": "Short File Identifier (SFI)", "8A": "Authorisation Response Code",
    "8C": "CDOL1", "8D": "CDOL2", "8E": "CVM List", "8F": "CA Public Key Index",
    "90": "Issuer Public Key Certificate", "92": "Issuer Public Key Remainder",
    "93": "Signed Static Application Data", "94": "Application File Locator (AFL)",
    "95": "Terminal Verification Results", "9A": "Transaction Date", "9B": "Transaction Status Information",
    "9C": "Transaction Type", "9D": "DDF Name", "9F02": "Amount, Authorised", "9F03": "Amount, Other",
    "9F06": "AID (Terminal)", "9F07": "Application Usage Control", "9F08": "Application Version Number",
    "9F09": "Application Version Number (Terminal)", "9F0D": "IAC - Default", "9F0E": "IAC - Denial",
    "9F0F": "IAC - Online", "9F10": "Issuer Application Data", "9F11": "Issuer Code Table Index",
    "9F12": "Application Preferred Name", "9F13": "Last Online ATC Register", "9F17": "PIN Try Counter",
    "9F1A": "Terminal Country Code", "9F1F": "Track 1 Discretionary Data", "9F21": "Transaction Time",
    "9F26": "Application Cryptogram", "9F27": "Cryptogram Information Data", "9F32": "Issuer Public Key Exponent",
    "9F33": "Terminal Capabilities", "9F34": "CVM Results", "9F35": "Terminal Type",
    "9F36": "Application Transaction Counter (ATC)", "9F37": "Unpredictable Number",
    "9F38": "PDOL", "9F42": "Application Currency Code", "9F44": "Application Currency Exponent",
    "9F46": "ICC Public Key Certificate", "9F47": "ICC Public Key Exponent",
    "9F48": "ICC Public Key Remainder", "9F49": "DDOL", "9F4A": "SDA Tag List",
    "9F4B": "Signed Dynamic Application Data", "9F4C": "ICC Dynamic Number", "9F4D": "Log Entry",
    "9F4E": "Merchant Name and Location", "9F6E": "Form Factor Indicator", "A5": "FCI Proprietary Template",
    "BF0C": "FCI Issuer Discretionary Data",
}
EMV_TEXT = {"50", "5F20", "9F12", "5F2D", "9F4E", "84"}
EMV_BCD = {"5A", "5F24", "5F25", "9A", "9F21", "5F28", "5F2A", "9F1A", "9F42", "9F02", "9F03", "5F34", "9C"}

CLASS_NAMES = ["UNIVERSAL", "APPLICATION", "CONTEXT", "PRIVATE"]


def decode_oid(b: bytes) -> str:
    if not b:
        raise DecodeError("빈 OID")
    parts, n = [], 0
    for i, x in enumerate(b):
        n = (n << 7) | (x & 0x7F)
        if not x & 0x80:
            parts.append(n)
            n = 0
    first = parts[0]
    head = [min(first // 40, 2), first - 40 * min(first // 40, 2)]
    return ".".join(str(p) for p in head + parts[1:])


def _ber_header(r: Reader):
    start = r.pos
    b = r.u8()
    cls, constructed, num = b >> 6, bool(b & 0x20), b & 0x1F
    if num == 0x1F:
        num = 0
        for i in range(5):
            x = r.u8()
            num = (num << 7) | (x & 0x7F)
            if not x & 0x80:
                break
        else:
            raise DecodeError("태그 번호가 너무 깁니다", start)
    tag_hex = r.data[start:r.pos].hex().upper()
    lb = r.u8()
    if lb < 0x80:
        length = lb
    elif lb == 0x80:
        if not constructed:
            raise DecodeError("primitive 태그에 부정 길이(0x80)", r.pos - 1)
        length = None
    else:
        n = lb & 0x7F
        if n > 4:
            raise DecodeError(f"길이 바이트 수 {n} 는 지원 범위 밖", r.pos - 1)
        length = r.uint(n)
    return cls, constructed, num, tag_hex, length


def _time_str(s: str, utc: bool) -> str:
    try:
        if utc:
            fmt = "%y%m%d%H%M%SZ" if len(s) == 13 else "%y%m%d%H%MZ"
        else:
            fmt = "%Y%m%d%H%M%SZ"
        return datetime.strptime(s, fmt).isoformat() + "Z"
    except ValueError:
        return s


def _bcd(b: bytes) -> str:
    return b.hex().upper()


def _primitive_value(data: bytes, cls: int, num: int, tag_hex: str, vstart: int, vend: int,
                     profile: str, depth: int, opts: dict) -> tuple[str, list[Node] | None]:
    b = data[vstart:vend]
    if profile == "emv":
        if tag_hex in EMV_TEXT:
            return bytes_value(b, ("latin-1",)), None
        if tag_hex in EMV_BCD:
            return f"{_bcd(b)}  (BCD)", None
        return f"{hexs(b)}", None
    if cls == 0:
        if num == 1:
            return ("TRUE" if b and b[0] else "FALSE"), None
        if num in (2, 10):
            v = int.from_bytes(b, "big", signed=True) if b else 0
            return (str(v) if len(b) <= 8 else f"{v}  (0x{b.hex().upper()})"), None
        if num == 5:
            return "NULL", None
        if num == 6:
            oid = decode_oid(b)
            name = OID_NAMES.get(oid)
            return (f"{oid}  ({name})" if name else oid), None
        if num in (12, 18, 19, 20, 21, 22, 25, 26, 27):
            return quote(b.decode("utf-8", errors="replace")), None
        if num == 30:
            return quote(b.decode("utf-16-be", errors="replace")), None
        if num == 28:
            return quote(b.decode("utf-32-be", errors="replace")), None
        if num == 23:
            return _time_str(b.decode("ascii", "replace"), True), None
        if num == 24:
            return _time_str(b.decode("ascii", "replace"), False), None
        if num == 3 and b:
            nested = _try_nested(data, vstart + 1, vend, profile, depth, opts)
            if nested is not None:
                return f"(미사용 비트 {b[0]}, 내부 DER)", nested
            return f"미사용 비트 {b[0]}, {hexs(b[1:])}", None
        if num == 4:
            nested = _try_nested(data, vstart, vend, profile, depth, opts)
            if nested is not None:
                return "(내부 DER)", nested
    elif cls == 2 and b:
        nested = _try_nested(data, vstart, vend, profile, depth, opts)
        if nested is not None:
            return "(내부 DER)", nested
    return bytes_value(b), None


def _try_nested(data: bytes, start: int, end: int, profile: str, depth: int, opts: dict) -> list[Node] | None:
    if end - start < 2 or depth >= opts["max_depth"] or data[start] not in (0x30, 0x31, 0x02, 0x04, 0x06, 0xA0):
        return None
    holder = Node(start, end - start, GROUP, "")
    pos = start
    try:
        while pos < end:
            pos = _ber_item(data, pos, end, holder, profile, depth + 1, opts)
    except DecodeError:
        return None
    if pos != end or any(c.kind == ERROR for c in holder.walk()):
        return None
    return holder.children


def _ber_label(cls: int, constructed: bool, num: int, tag_hex: str, profile: str) -> str:
    if profile == "emv":
        name = EMV_TAGS.get(tag_hex)
        return f"{tag_hex} {name}" if name else tag_hex
    if cls == 0:
        return UNIVERSAL.get(num, f"UNIVERSAL {num}")
    if cls == 2:
        return f"[{num}]"
    return f"[{CLASS_NAMES[cls]} {num}]"


def _ber_item(data: bytes, pos: int, end: int, parent: Node, profile: str, depth: int, opts: dict) -> int:
    r = Reader(data, pos, end)
    cls, constructed, num, tag_hex, length = _ber_header(r)
    label = _ber_label(cls, constructed, num, tag_hex, profile)
    vstart = r.pos
    if constructed:
        g = parent.add(Node(pos, 0, GROUP, label, close="}"))
        if length is None:  # indefinite: until 00 00
            p = vstart
            while True:
                if p + 2 > end:
                    raise DecodeError("부정 길이 끝(00 00)을 찾지 못했습니다", p)
                if data[p] == 0 and data[p + 1] == 0:
                    p += 2
                    break
                p = _ber_item(data, p, end, g, profile, depth + 1, opts)
            g.length = p - pos
            g.note = "부정 길이"
            return p
        r.need(length)
        vend = vstart + length
        g.length = vend - pos
        p = vstart
        while p < vend:
            p = _ber_item(data, p, vend, g, profile, depth + 1, opts)
        return vend
    r.need(length)
    vend = vstart + length
    value, nested = _primitive_value(data, cls, num, tag_hex, vstart, vend, profile, depth, opts)
    if nested is not None:
        g = parent.add(Node(pos, vend - pos, GROUP, label, value, close="}"))
        g.children.extend(nested)
    else:
        parent.add(Node(pos, vend - pos, VALUE, label, value))
    return vend


_PEM_RE = re.compile(rb"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.S)


def decode_ber(data: bytes, opts: dict) -> Node:
    profile = opts["profile"]
    m = _PEM_RE.search(data) if data[:64].lstrip().startswith(b"-----BEGIN") else None
    if m:
        # PEM: decode base64 body; offsets refer to the decoded DER bytes.
        der = base64.b64decode(re.sub(rb"\s+", b"", m.group(2)))
        root = new_root(f"PEM {m.group(1).decode()} (DER 변환 후 오프셋)", der)
        return run_stream(root, der, lambda p, parent: _ber_item(der, p, len(der), parent, profile, 0, opts))
    root = new_root("BER-TLV" if profile == "asn1" else "EMV TLV", data)
    return run_stream(root, data, lambda p, parent: _ber_item(data, p, len(data), parent, profile, 0, opts))


register(Decoder(
    "ber", "BER-TLV / ASN.1 (DER·BER, X.509, PEM)", "TLV",
    "ASN.1 BER/DER 인코딩. 다중 바이트 태그, 장/단/부정 길이, constructed 재귀, OID·시간·문자열 해석. PEM 자동 변환.",
    decode_ber,
    [Option("profile", "프로필", "choice", "asn1", [("asn1", "ASN.1 (X.509 등)"), ("emv", "EMV/스마트카드")]),
     Option("max_depth", "내부 DER 추정 깊이", "int", 32, minimum=0, maximum=256)],
))

register(Decoder(
    "emv", "EMV / 스마트카드 BER-TLV", "TLV",
    "EMV 태그 사전(9F02, 5F20 등)으로 이름 표시. 텍스트·BCD 태그 해석.",
    decode_ber,
    [Option("profile", "프로필", "choice", "emv", [("emv", "EMV/스마트카드"), ("asn1", "ASN.1")]),
     Option("max_depth", "내부 DER 추정 깊이", "int", 32, minimum=0, maximum=256)],
))

"""The one pcap format the backend writes (the spool's saved files and the live Wireshark pipe): the classic
little-endian header, version 2.4, snaplen 262144, linktype 1 (Ethernet), microsecond timestamps. Written with
`struct`; no Scapy."""
import errno
import os
import struct

GLOBAL = struct.Struct("<IHHiIII")
RECORD = struct.Struct("<IIII")
MAGIC = 0xA1B2C3D4
SNAPLEN = 262144
LINKTYPE_ETHERNET = 1
HEADER = GLOBAL.pack(MAGIC, 2, 4, 0, 0, SNAPLEN, LINKTYPE_ETHERNET)


def pack_record(ts_rel: float, raw: bytes, wirelen: int) -> bytes:
    """One record: `ts_rel` seconds as sec/usec, incl_len = len(raw), orig_len = wirelen, then the bytes."""
    sec, usec = divmod(round(ts_rel * 1_000_000), 1_000_000)
    return RECORD.pack(sec, usec, len(raw), wirelen) + raw


def write_all(fd: int, data: bytes) -> None:
    """os.write until every byte is out (it may take fewer); an OSError propagates, a 0-byte write is EIO."""
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n == 0:
            raise OSError(errno.EIO, "write returned 0")
        view = view[n:]

from __future__ import annotations

import struct

import pytest

from rns_portfwd.constants import (
    MSG_CLOSE,
    MSG_DATA,
    MSG_OPEN,
    MSG_OPEN_ERR,
    MSG_OPEN_OK,
    MSG_WINDOW,
    CloseReason,
    ErrorCode,
)
from rns_portfwd.errors import ProtocolError
from rns_portfwd.protocol import (
    MESSAGE_TYPES,
    CloseMessage,
    DataMessage,
    OpenErrMessage,
    OpenMessage,
    OpenOkMessage,
    WindowMessage,
    register_message_types,
)


def roundtrip(message, cls):
    raw = message.pack()
    decoded = cls()
    decoded.unpack(raw)
    return decoded


def test_message_types_are_unique_and_in_application_range():
    values = [cls.MSGTYPE for cls in MESSAGE_TYPES]
    assert len(set(values)) == len(values)
    assert values == [MSG_OPEN, MSG_OPEN_OK, MSG_OPEN_ERR, MSG_DATA, MSG_WINDOW, MSG_CLOSE]
    assert all(0xAC00 <= value <= 0xAC0F for value in values)


def test_open_roundtrip():
    message = roundtrip(OpenMessage(7, 1, "ssh", "pa:ss"), OpenMessage)
    assert (message.stream_id, message.version, message.service, message.password) == (
        7,
        1,
        "ssh",
        "pa:ss",
    )


def test_open_ok_roundtrip():
    message = roundtrip(OpenOkMessage(7, 4096), OpenOkMessage)
    assert message.stream_id == 7
    assert message.credit == 4096


def test_open_err_roundtrip():
    message = roundtrip(OpenErrMessage(7, ErrorCode.AUTH), OpenErrMessage)
    assert message.stream_id == 7
    assert message.code == ErrorCode.AUTH


def test_data_roundtrip_and_flags():
    message = roundtrip(
        DataMessage(9, b"hello", eof=True, compressed=True), DataMessage
    )
    assert message.stream_id == 9
    assert message.data == b"hello"
    assert message.eof is True
    assert message.compressed is True


def test_window_and_close_roundtrip():
    window = roundtrip(WindowMessage(3, 1024), WindowMessage)
    assert (window.stream_id, window.credit) == (3, 1024)
    close = roundtrip(CloseMessage(3, CloseReason.NORMAL), CloseMessage)
    assert (close.stream_id, close.reason) == (3, CloseReason.NORMAL)


def test_malformed_messages_are_rejected():
    with pytest.raises(ProtocolError):
        OpenMessage().unpack(b"\x00")
    with pytest.raises(ProtocolError):
        OpenMessage().unpack(struct.pack(">HBHH", 1, 1, 10, 0) + b"short")
    with pytest.raises(ProtocolError):
        OpenMessage().pack()
    with pytest.raises(ProtocolError):
        OpenOkMessage(1, 0).pack()
    with pytest.raises(ProtocolError):
        OpenOkMessage(1, 1).unpack(b"\x00" * 5)
    with pytest.raises(ProtocolError):
        OpenErrMessage(1, 99).pack()
    with pytest.raises(ProtocolError):
        DataMessage().unpack(b"\x00\x01")
    with pytest.raises(ProtocolError):
        DataMessage().unpack(struct.pack(">HB", 0x4000, 0))
    with pytest.raises(ProtocolError):
        DataMessage().unpack(struct.pack(">HB", 1, 0x80))
    with pytest.raises(ProtocolError):
        WindowMessage(1, 0).pack()
    with pytest.raises(ProtocolError):
        CloseMessage(1, 99).pack()


def test_open_truncated_and_trailing():
    packed = OpenMessage(1, 1, "ssh", "pw").pack()
    with pytest.raises(ProtocolError):
        OpenMessage().unpack(packed[:-1])
    with pytest.raises(ProtocolError):
        OpenMessage().unpack(packed + b"x")


def test_register_message_types():
    class Channel:
        def __init__(self):
            self.registered = []

        def register_message_type(self, cls):
            self.registered.append(cls)

    channel = Channel()
    register_message_types(channel)
    assert channel.registered == list(MESSAGE_TYPES)

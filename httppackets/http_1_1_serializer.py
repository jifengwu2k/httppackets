# Copyright (c) 2026 Jifeng Wu
# Licensed under the MIT License. See LICENSE file in the project root
# for full license information.
from typing import (
    BinaryIO,
    Dict,
    List,
    Optional,
    Union,
)


class SupportsRead(object):
    """Source of streaming body data for serialization.

    Implement ``read()`` to supply body data in chunks.
    Return ``b""`` to signal exhaustion.
    """

    __slots__ = ()

    def read(self, n=-1):
        # type: (int) -> Union[bytes, bytearray]
        raise NotImplementedError()


class SerializerError(Exception):
    """Base class for serialization errors."""

    __slots__ = ()


class HeaderValueError(SerializerError):
    """A header name or value contains forbidden characters."""

    __slots__ = ()


CRLF = b"\r\n"
CHUNK_SIZE = 65536


def validate_header_name(name):
    # type: (str) -> None
    for ch in name:
        if ch in "\r\n":
            raise HeaderValueError(
                "header name contains forbidden character: %r" % (ch,)
            )


def validate_header_value(value):
    # type: (str) -> None
    for ch in value:
        if ch in "\r\n":
            raise HeaderValueError(
                "header value contains forbidden character: %r" % (ch,)
            )


def write_to_stream(stream, data):
    # type: (BinaryIO, bytes) -> None
    stream.write(data)


def format_headers(headers):
    # type: (Dict[str, List[str]]) -> List[bytes]
    result = []  # type: List[bytes]
    for name, values in headers.items():
        validate_header_name(name)
        for value in values:
            validate_header_value(value)
            result.append(
                ("%s: %s\r\n" % (name, value)).encode("latin-1")
            )
    return result


def write_headers(stream, headers):
    # type: (BinaryIO, Dict[str, List[str]]) -> None
    for line in format_headers(headers):
        write_to_stream(stream, line)


def write_chunked_body(stream, body):
    # type: (BinaryIO, SupportsRead) -> None
    while True:
        chunk = body.read(CHUNK_SIZE)
        if not chunk:
            break
        chunk = bytes(chunk)
        write_to_stream(stream, ("%x\r\n" % (len(chunk),)).encode("ascii"))
        write_to_stream(stream, chunk)
        write_to_stream(stream, CRLF)
    write_to_stream(stream, b"0\r\n\r\n")


def write_headers_and_body(stream, headers, body):
    # type: (BinaryIO, Dict[str, List[str]], Optional[Union[bytes, SupportsRead]]) -> None
    if body is None:
        write_headers(stream, headers)
        write_to_stream(stream, CRLF)
        return

    if isinstance(body, bytes):
        headers = dict(headers)
        headers["content-length"] = [str(len(body))]
        write_headers(stream, headers)
        write_to_stream(stream, CRLF)
        write_to_stream(stream, body)
        return

    # SupportsRead -- chunked transfer-encoding.
    headers = dict(headers)
    headers["transfer-encoding"] = ["chunked"]
    write_headers(stream, headers)
    write_to_stream(stream, CRLF)
    write_chunked_body(stream, body)


def serialize_http_1_1_request(stream, method, target, headers, body=None):
    # type: (BinaryIO, str, str, Dict[str, List[str]], Optional[Union[bytes, SupportsRead]]) -> None
    """Write a single HTTP/1.1 request to *stream*.

    *body* controls framing:

    - ``None`` -- no body.
    - ``bytes`` -- Content-Length framing.
    - ``SupportsRead`` -- chunked transfer-encoding.
    """
    write_to_stream(
        stream,
        ("%s %s HTTP/1.1\r\n" % (method, target)).encode("latin-1"),
    )
    write_headers_and_body(stream, headers, body)


def serialize_http_1_1_response(stream, status_code, reason, headers, body=None):
    # type: (BinaryIO, int, str, Dict[str, List[str]], Optional[Union[bytes, SupportsRead]]) -> None
    """Write a single HTTP/1.1 response to *stream*.

    *body* controls framing:

    - ``None`` -- no body.
    - ``bytes`` -- Content-Length framing.
    - ``SupportsRead`` -- chunked transfer-encoding.
    """
    write_to_stream(
        stream,
        ("HTTP/1.1 %d %s\r\n" % (status_code, reason)).encode("latin-1"),
    )
    write_headers_and_body(stream, headers, body)

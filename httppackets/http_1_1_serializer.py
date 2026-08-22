# Copyright (c) 2026 Jifeng Wu
# Licensed under the MIT License. See LICENSE file in the project root
# for full license information.
import string

from six import integer_types, text_type

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


class ConflictingFramingError(SerializerError):
    """The caller set a framing header the serializer manages itself."""

    __slots__ = ()


class StartLineValueError(SerializerError):
    """A request or response start-line value is invalid."""

    __slots__ = ()


CRLF = b"\r\n"
CHUNK_SIZE = 65536

# RFC 7230 token characters, the only characters permitted in a field-name.
HEADER_NAME_TOKEN_CHARS = frozenset(
    "!#$%&'*+-.^_`|~" + string.digits + string.ascii_letters
)


def validate_header_name(name):
    # type: (text_type) -> None
    if not name:
        raise HeaderValueError("header name must not be empty")
    for ch in name:
        if ch not in HEADER_NAME_TOKEN_CHARS:
            raise HeaderValueError(
                "header name contains forbidden character: %r" % (ch,)
            )


def validate_header_value(value):
    # type: (text_type) -> None
    for ch in value:
        code = ord(ch)
        if not (
            code == 9
            or 32 <= code <= 126
            or 128 <= code <= 255
        ):
            raise HeaderValueError(
                "header value contains forbidden character: %r" % (ch,)
            )


def validate_method(method):
    # type: (text_type) -> None
    if not method:
        raise StartLineValueError("method must not be empty")
    for ch in method:
        if ch not in HEADER_NAME_TOKEN_CHARS:
            raise StartLineValueError(
                "method contains forbidden character: %r" % (ch,)
            )


def validate_target(target):
    # type: (text_type) -> None
    if not target:
        raise StartLineValueError("request target must not be empty")
    for ch in target:
        code = ord(ch)
        if code <= 32 or code == 127 or code > 255:
            raise StartLineValueError(
                "request target contains forbidden character: %r" % (ch,)
            )


def validate_status_code(status_code):
    # type: (int) -> None
    if not isinstance(status_code, integer_types):
        raise StartLineValueError("status code must be an integer")
    if status_code < 100 or status_code > 599:
        raise StartLineValueError("status code must be between 100 and 599")


def validate_reason(reason):
    # type: (text_type) -> None
    for ch in reason:
        code = ord(ch)
        if not (
            code == 9
            or 32 <= code <= 126
            or 128 <= code <= 255
        ):
            raise StartLineValueError(
                "reason phrase contains forbidden character: %r" % (ch,)
            )


def write_to_stream(stream, data):
    # type: (BinaryIO, bytes) -> None
    stream.write(data)


def to_text(value):
    # type: (Union[bytes, text_type]) -> text_type
    # On Python 2 a byte ``str`` carrying 0x80-0xFF cannot be re-encoded to
    # latin-1 without an implicit (and failing) ASCII decode first. Decode any
    # byte input as latin-1 so the same value serializes identically on both
    # Python 2 and Python 3.
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return value


def format_headers(headers):
    # type: (Dict[text_type, List[text_type]]) -> List[bytes]
    result = []  # type: List[bytes]
    for name, values in headers.items():
        name = to_text(name)
        validate_header_name(name)
        for value in values:
            value = to_text(value)
            validate_header_value(value)
            result.append(
                (u"%s: %s\r\n" % (name, value)).encode("latin-1")
            )
    return result


def write_headers(stream, headers):
    # type: (BinaryIO, Dict[text_type, List[text_type]]) -> None
    for line in format_headers(headers):
        write_to_stream(stream, line)


def check_no_framing_headers(headers):
    # type: (Dict[text_type, List[text_type]]) -> None
    for name in headers:
        lowered = to_text(name).lower()
        if lowered == u"content-length" or lowered == u"transfer-encoding":
            raise ConflictingFramingError(
                "caller must not set %r; framing is managed by the serializer"
                % (name,)
            )


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


def validate_headers_for_body(headers, body):
    # type: (Dict[text_type, List[text_type]], Optional[Union[bytes, SupportsRead]]) -> None
    if body is not None:
        check_no_framing_headers(headers)
    format_headers(headers)


def write_headers_and_body(stream, headers, body):
    # type: (BinaryIO, Dict[text_type, List[text_type]], Optional[Union[bytes, SupportsRead]]) -> None
    if body is None:
        write_headers(stream, headers)
        write_to_stream(stream, CRLF)
        return

    check_no_framing_headers(headers)
    write_headers(stream, headers)

    if isinstance(body, bytes):
        write_to_stream(
            stream,
            ("content-length: %d\r\n" % (len(body),)).encode("ascii"),
        )
        write_to_stream(stream, CRLF)
        write_to_stream(stream, body)
        return

    # SupportsRead -- chunked transfer-encoding.
    write_to_stream(stream, b"transfer-encoding: chunked\r\n")
    write_to_stream(stream, CRLF)
    write_chunked_body(stream, body)


def serialize_http_1_1_request(stream, method, target, headers, body=None):
    # type: (BinaryIO, text_type, text_type, Dict[text_type, List[text_type]], Optional[Union[bytes, SupportsRead]]) -> None
    """Write a single HTTP/1.1 request to *stream*.

    *body* controls framing:

    - ``None`` -- no body.
    - ``bytes`` -- Content-Length framing.
    - ``SupportsRead`` -- chunked transfer-encoding.
    """
    method = to_text(method)
    target = to_text(target)
    validate_method(method)
    validate_target(target)
    validate_headers_for_body(headers, body)

    write_to_stream(
        stream,
        (u"%s %s HTTP/1.1\r\n" % (method, target)).encode("latin-1"),
    )
    write_headers_and_body(stream, headers, body)


def serialize_http_1_1_response(stream, status_code, reason, headers, body=None):
    # type: (BinaryIO, int, text_type, Dict[text_type, List[text_type]], Optional[Union[bytes, SupportsRead]]) -> None
    """Write a single HTTP/1.1 response to *stream*.

    *body* controls framing:

    - ``None`` -- no body.
    - ``bytes`` -- Content-Length framing.
    - ``SupportsRead`` -- chunked transfer-encoding.
    """
    reason = to_text(reason)
    validate_status_code(status_code)
    validate_reason(reason)
    validate_headers_for_body(headers, body)

    write_to_stream(
        stream,
        (u"HTTP/1.1 %d %s\r\n" % (status_code, reason)).encode("latin-1"),
    )
    write_headers_and_body(stream, headers, body)

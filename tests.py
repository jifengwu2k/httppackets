# Copyright (c) 2026 Jifeng Wu
# Licensed under the MIT License. See LICENSE file in the project root
# for full license information.
"""Self-contained test suite for ``httppackets``.

Runs unchanged on Python 2 and Python 3, POSIX and NT. It uses only
in-memory ``io.BytesIO`` streams, so it needs no filesystem, network, or
platform-specific facilities. Run it directly::

    python tests.py

The process exits non-zero if any test fails.
"""
from __future__ import print_function

import io
import sys

from six import text_type

from httppackets.http_1_1_parser import (
    parse_http_1_1_requests,
    parse_http_1_1_responses,
    Decision,
    MalformedRequestLine,
    MalformedStatusLine,
    MalformedHeader,
    UnsupportedHTTPVersion,
    InvalidFraming,
    UnsupportedTransferEncoding,
    PrematureEOF,
    BodyNotConsumedError,
    LineTooLong,
    HeaderSectionTooLarge,
)
from httppackets.http_1_1_serializer import (
    serialize_http_1_1_request,
    serialize_http_1_1_response,
    SupportsRead,
    HeaderValueError,
    ConflictingFramingError,
    StartLineValueError,
)


# --------------------------------------------------------------------------
# Test helpers
# --------------------------------------------------------------------------

class BytesChunks(SupportsRead):
    """A ``SupportsRead`` body backed by an in-memory list of byte chunks."""

    __slots__ = ("chunks",)

    def __init__(self, chunks):
        # type: (list) -> None
        self.chunks = list(chunks)

    def read(self, n=-1):
        # type: (int) -> bytes
        if not self.chunks:
            return b""
        return self.chunks.pop(0)


def assert_raises(error_type, func):
    # type: (type, object) -> None
    try:
        func()
    except error_type:
        return
    raise AssertionError("expected %s to be raised" % (error_type.__name__,))


def collect_requests(raw, decide=None):
    # type: (bytes, object) -> list
    """Parse *raw* and return one dict per request, with the body read."""
    results = []

    def on_headers(method, target, headers):
        results.append(
            {"method": method, "target": target, "headers": headers, "body": None}
        )
        if decide is None:
            return Decision.READ_BODY
        return decide(method, target, headers)

    def on_body(reader):
        results[-1]["body"] = bytes(reader.read())

    parse_http_1_1_requests(io.BytesIO(raw), on_headers=on_headers, on_body=on_body)
    return results


def collect_responses(raw, decide=None, request_methods=None):
    # type: (bytes, object, object) -> list
    results = []

    def on_headers(status_code, reason, headers):
        results.append(
            {
                "status_code": status_code,
                "reason": reason,
                "headers": headers,
                "body": None,
            }
        )
        if decide is None:
            return Decision.READ_BODY
        return decide(status_code, reason, headers)

    def on_body(reader):
        results[-1]["body"] = bytes(reader.read())

    parse_http_1_1_responses(
        io.BytesIO(raw),
        on_headers=on_headers,
        on_body=on_body,
        request_methods=request_methods,
    )
    return results


# --------------------------------------------------------------------------
# Request parsing
# --------------------------------------------------------------------------

def test_parse_simple_get():
    raw = b"GET /hello HTTP/1.1\r\nHost: example.com\r\n\r\n"
    reqs = collect_requests(raw)
    assert len(reqs) == 1
    assert reqs[0]["method"] == u"GET"
    assert reqs[0]["target"] == u"/hello"
    assert reqs[0]["headers"] == {u"host": [u"example.com"]}
    assert reqs[0]["body"] is None


def test_parse_post_content_length():
    raw = (
        b"POST /submit HTTP/1.1\r\n"
        b"Content-Length: 13\r\n"
        b"\r\n"
        b"Hello, World!"
    )
    reqs = collect_requests(raw)
    assert len(reqs) == 1
    assert reqs[0]["body"] == b"Hello, World!"


def test_parse_chunked_request():
    raw = (
        b"POST /upload HTTP/1.1\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"\r\n"
        b"5\r\nHello\r\n6\r\n World\r\n0\r\n\r\n"
    )
    reqs = collect_requests(raw)
    assert reqs[0]["body"] == b"Hello World"


def test_parse_multiple_requests():
    raw = (
        b"GET /a HTTP/1.1\r\nHost: h\r\n\r\n"
        b"POST /b HTTP/1.1\r\nContent-Length: 2\r\n\r\nhi"
    )
    reqs = collect_requests(raw)
    assert [r["target"] for r in reqs] == [u"/a", u"/b"]
    assert reqs[1]["body"] == b"hi"


def test_parse_discard_body_continues():
    raw = (
        b"POST /a HTTP/1.1\r\nContent-Length: 2\r\n\r\nhi"
        b"GET /b HTTP/1.1\r\nHost: h\r\n\r\n"
    )
    reqs = collect_requests(raw, decide=lambda m, t, h: Decision.DISCARD_BODY)
    assert len(reqs) == 2
    # Body was drained, never handed to on_body.
    assert reqs[0]["body"] is None


def test_parse_reject_stops_parsing():
    raw = (
        b"POST /a HTTP/1.1\r\nContent-Length: 2\r\n\r\nhi"
        b"GET /b HTTP/1.1\r\nHost: h\r\n\r\n"
    )
    reqs = collect_requests(raw, decide=lambda m, t, h: Decision.REJECT)
    assert len(reqs) == 1


def test_parse_content_length_zero_has_no_body():
    raw = b"POST /a HTTP/1.1\r\nContent-Length: 0\r\n\r\n"
    reqs = collect_requests(raw)
    assert reqs[0]["body"] is None


def test_parse_request_target_and_value_are_text():
    # Bytes 0x80-0xFF are legal in a request target and a header value; the
    # parser must return them as text (unicode on Py2, str on Py3), preserving
    # the byte via latin-1 -- identically on both Python versions.
    raw = b"GET /caf\xe9 HTTP/1.1\r\nX-Note: caf\xe9\r\n\r\n"
    reqs = collect_requests(raw)
    target = reqs[0]["target"]
    value = reqs[0]["headers"][u"x-note"][0]
    assert target == u"/caf\xe9"
    assert value == u"caf\xe9"
    assert isinstance(target, text_type)
    assert isinstance(value, text_type)


def test_parse_body_not_consumed_error():
    raw = b"POST /a HTTP/1.1\r\nContent-Length: 13\r\n\r\nHello, World!"

    def on_headers(method, target, headers):
        return Decision.READ_BODY

    def on_body(reader):
        reader.read(1)  # leave the rest unread

    assert_raises(
        BodyNotConsumedError,
        lambda: parse_http_1_1_requests(
            io.BytesIO(raw), on_headers=on_headers, on_body=on_body
        ),
    )


def test_parse_premature_eof_in_body():
    raw = b"POST /a HTTP/1.1\r\nContent-Length: 13\r\n\r\nshort"
    assert_raises(PrematureEOF, lambda: collect_requests(raw))


def test_parse_malformed_request_line():
    raw = b"GET\r\n\r\n"
    assert_raises(MalformedRequestLine, lambda: collect_requests(raw))


def test_parse_unsupported_version():
    raw = b"GET / HTTP/1.0\r\n\r\n"
    assert_raises(UnsupportedHTTPVersion, lambda: collect_requests(raw))


def test_parse_folded_header_rejected():
    raw = b"GET / HTTP/1.1\r\nX-A: one\r\n two\r\n\r\n"
    assert_raises(MalformedHeader, lambda: collect_requests(raw))


def test_parse_conflicting_framing():
    raw = (
        b"POST /a HTTP/1.1\r\n"
        b"Content-Length: 2\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"\r\n"
    )
    assert_raises(InvalidFraming, lambda: collect_requests(raw))


def test_parse_unsupported_transfer_encoding():
    raw = b"POST /a HTTP/1.1\r\nTransfer-Encoding: gzip\r\n\r\n"
    assert_raises(UnsupportedTransferEncoding, lambda: collect_requests(raw))


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------

def test_parse_response_content_length():
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\n\r\nHello, World!"
    resps = collect_responses(raw)
    assert resps[0]["status_code"] == 200
    assert resps[0]["reason"] == u"OK"
    assert resps[0]["body"] == b"Hello, World!"


def test_parse_response_reason_is_text():
    raw = b"HTTP/1.1 200 caf\xe9\r\nContent-Length: 0\r\n\r\n"
    resps = collect_responses(raw)
    assert resps[0]["reason"] == u"caf\xe9"
    assert isinstance(resps[0]["reason"], text_type)


def test_parse_multiple_responses():
    raw = (
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi"
        b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
    )
    resps = collect_responses(raw)
    assert [r["status_code"] for r in resps] == [200, 404]


def test_parse_malformed_status_line():
    raw = b"HTTP/1.1 OK\r\n\r\n"
    assert_raises(MalformedStatusLine, lambda: collect_responses(raw))


def test_parse_response_empty_reason():
    raw = b"HTTP/1.1 200 \r\nContent-Length: 0\r\n\r\n"
    resps = collect_responses(raw)
    assert resps[0]["reason"] == u""


def test_parse_close_delimited_response():
    raw = b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nHello"
    resps = collect_responses(raw)
    assert resps[0]["body"] == b"Hello"


def test_parse_304_does_not_consume_next_response():
    raw = (
        b"HTTP/1.1 304 Not Modified\r\nContent-Length: 5\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
    )
    resps = collect_responses(raw)
    assert [response["status_code"] for response in resps] == [304, 200]
    assert resps[0]["body"] is None


def test_parse_head_response_with_request_context():
    raw = (
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
    )
    resps = collect_responses(raw, request_methods=[u"HEAD", u"GET"])
    assert [response["status_code"] for response in resps] == [200, 200]
    assert resps[0]["body"] is None


def test_parse_connect_response_leaves_tunnel_bytes():
    raw = b"HTTP/1.1 200 Connection Established\r\n\r\ntunnel"
    stream = io.BytesIO(raw)
    seen = []

    def on_headers(status_code, reason, headers):
        seen.append(status_code)
        return Decision.READ_BODY

    parse_http_1_1_responses(
        stream,
        on_headers=on_headers,
        on_body=lambda reader: reader.read(),
        request_methods=[u"CONNECT"],
    )
    assert seen == [200]
    assert stream.read() == b"tunnel"


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------

def test_serialize_request_bytes_body():
    out = io.BytesIO()
    serialize_http_1_1_request(
        out,
        method=u"POST",
        target=u"/submit",
        headers={u"host": [u"example.com"]},
        body=b"Hello, World!",
    )
    assert out.getvalue() == (
        b"POST /submit HTTP/1.1\r\n"
        b"host: example.com\r\n"
        b"content-length: 13\r\n"
        b"\r\n"
        b"Hello, World!"
    )


def test_serialize_request_no_body():
    out = io.BytesIO()
    serialize_http_1_1_request(
        out, method=u"GET", target=u"/hello", headers={u"host": [u"example.com"]}
    )
    assert out.getvalue() == b"GET /hello HTTP/1.1\r\nhost: example.com\r\n\r\n"


def test_serialize_chunked_request():
    out = io.BytesIO()
    serialize_http_1_1_request(
        out,
        method=u"POST",
        target=u"/u",
        headers={u"host": [u"h"]},
        body=BytesChunks([b"Hello", b" World"]),
    )
    assert out.getvalue() == (
        b"POST /u HTTP/1.1\r\n"
        b"host: h\r\n"
        b"transfer-encoding: chunked\r\n"
        b"\r\n"
        b"5\r\nHello\r\n6\r\n World\r\n0\r\n\r\n"
    )


def test_serialize_response_bytes_body():
    out = io.BytesIO()
    serialize_http_1_1_response(
        out,
        status_code=200,
        reason=u"OK",
        headers={u"content-type": [u"application/json"]},
        body=b'{"status":"ok"}',
    )
    assert out.getvalue() == (
        b"HTTP/1.1 200 OK\r\n"
        b"content-type: application/json\r\n"
        b"content-length: 15\r\n"
        b"\r\n"
        b'{"status":"ok"}'
    )


def test_serialize_conflicting_content_length():
    out = io.BytesIO()
    assert_raises(
        ConflictingFramingError,
        lambda: serialize_http_1_1_request(
            out, u"POST", u"/a", {u"Content-Length": [u"2"]}, body=b"hi"
        ),
    )


def test_serialize_conflicting_transfer_encoding():
    out = io.BytesIO()
    assert_raises(
        ConflictingFramingError,
        lambda: serialize_http_1_1_request(
            out,
            u"POST",
            u"/a",
            {u"Transfer-Encoding": [u"chunked"]},
            body=BytesChunks([b"hi"]),
        ),
    )


def test_serialize_empty_header_name():
    out = io.BytesIO()
    assert_raises(
        HeaderValueError,
        lambda: serialize_http_1_1_request(out, u"GET", u"/", {u"": [u"v"]}),
    )


def test_serialize_bad_header_name_char():
    out = io.BytesIO()
    assert_raises(
        HeaderValueError,
        lambda: serialize_http_1_1_request(out, u"GET", u"/", {u"bad name": [u"v"]}),
    )


def test_serialize_crlf_in_header_value():
    out = io.BytesIO()
    assert_raises(
        HeaderValueError,
        lambda: serialize_http_1_1_request(out, u"GET", u"/", {u"x": [u"a\r\nb"]}),
    )


def test_serialize_control_in_header_value():
    out = io.BytesIO()
    assert_raises(
        HeaderValueError,
        lambda: serialize_http_1_1_request(out, u"GET", u"/", {u"x": [u"a\x00b"]}),
    )
    assert out.getvalue() == b""


def test_serialize_bytes_framing_header_conflict():
    out = io.BytesIO()
    assert_raises(
        ConflictingFramingError,
        lambda: serialize_http_1_1_request(
            out, u"POST", u"/", {b"Content-Length": [b"999"]}, body=b"x"
        ),
    )
    assert out.getvalue() == b""


def test_serialize_start_line_injection():
    out = io.BytesIO()
    assert_raises(
        StartLineValueError,
        lambda: serialize_http_1_1_request(
            out, u"GET\r\nX-Evil: yes", u"/", {}
        ),
    )
    assert out.getvalue() == b""


def test_serialize_invalid_status_code():
    out = io.BytesIO()
    assert_raises(
        StartLineValueError,
        lambda: serialize_http_1_1_response(out, 20, u"OK", {}),
    )
    assert out.getvalue() == b""


def test_serialize_reason_injection():
    out = io.BytesIO()
    assert_raises(
        StartLineValueError,
        lambda: serialize_http_1_1_response(
            out, 200, u"OK\r\nX-Evil: yes", {}
        ),
    )
    assert out.getvalue() == b""


def test_serialize_bytes_and_text_are_equivalent():
    # A high byte given as text or as raw bytes must produce identical output,
    # on both Python 2 and Python 3.
    text_out = io.BytesIO()
    serialize_http_1_1_response(
        text_out, 200, u"caf\xe9", {u"x": [u"v\xe9"]}
    )
    bytes_out = io.BytesIO()
    serialize_http_1_1_response(
        bytes_out, 200, b"caf\xe9", {b"x": [b"v\xe9"]}
    )
    expected = b"HTTP/1.1 200 caf\xe9\r\nx: v\xe9\r\n\r\n"
    assert text_out.getvalue() == expected
    assert bytes_out.getvalue() == expected


# --------------------------------------------------------------------------
# Round trips
# --------------------------------------------------------------------------

def test_round_trip_request():
    out = io.BytesIO()
    serialize_http_1_1_request(
        out, u"POST", u"/caf\xe9", {u"x-note": [u"caf\xe9"]}, body=b"payload"
    )
    reqs = collect_requests(out.getvalue())
    assert reqs[0]["method"] == u"POST"
    assert reqs[0]["target"] == u"/caf\xe9"
    assert reqs[0]["headers"][u"x-note"] == [u"caf\xe9"]
    assert reqs[0]["body"] == b"payload"


def test_round_trip_response():
    out = io.BytesIO()
    serialize_http_1_1_response(out, 201, u"Created", {u"x": [u"y"]}, body=b"ok")
    resps = collect_responses(out.getvalue())
    assert resps[0]["status_code"] == 201
    assert resps[0]["reason"] == u"Created"
    assert resps[0]["body"] == b"ok"


# --------------------------------------------------------------------------
# Chunked body error paths
# --------------------------------------------------------------------------

def test_parse_chunked_missing_crlf_after_chunk():
    # Chunk data followed by two bytes that are not CRLF.
    raw = b"POST /a HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nHelloXX"
    assert_raises(InvalidFraming, lambda: collect_requests(raw))


def test_parse_chunked_eof_after_chunk_data():
    # Stream ends where the chunk-terminating CRLF should be.
    raw = b"POST /a HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nHello"
    assert_raises(PrematureEOF, lambda: collect_requests(raw))


def test_parse_chunked_eof_in_chunk_data():
    # Declared chunk size exceeds the bytes actually available.
    raw = b"POST /a HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nHi"
    assert_raises(PrematureEOF, lambda: collect_requests(raw))


def test_parse_chunked_eof_reading_size():
    # Stream ends where the next chunk-size line should begin.
    raw = b"POST /a HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
    assert_raises(PrematureEOF, lambda: collect_requests(raw))


def test_parse_chunked_with_trailers():
    # Trailer fields are parsed, validated, and discarded.
    raw = (
        b"POST /a HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"5\r\nHello\r\n0\r\nX-Trailer: value\r\n\r\n"
    )
    reqs = collect_requests(raw)
    assert reqs[0]["body"] == b"Hello"


def test_parse_valid_chunk_extensions():
    raw = (
        b"POST /a HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"5;name=token;quoted=\"a b\"\r\nHello\r\n0\r\n\r\n"
    )
    reqs = collect_requests(raw)
    assert reqs[0]["body"] == b"Hello"


def test_parse_invalid_chunk_extensions():
    invalid_extensions = [b";", b";=", b"; bad"]
    for extension in invalid_extensions:
        raw = (
            b"POST /a HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"1" + extension + b"\r\nx\r\n0\r\n\r\n"
        )
        assert_raises(InvalidFraming, lambda: collect_requests(raw))


def test_parse_chunked_eof_in_trailers():
    # Stream ends after the final zero chunk, before the terminating CRLF.
    raw = b"POST /a HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n"
    assert_raises(PrematureEOF, lambda: collect_requests(raw))


# --------------------------------------------------------------------------
# Premature EOF while reading the head
# --------------------------------------------------------------------------

def test_parse_premature_eof_partial_request_line():
    raw = b"GET / HTTP/1.1"  # no terminating CRLF
    assert_raises(PrematureEOF, lambda: collect_requests(raw))


def test_parse_premature_eof_in_headers():
    raw = b"GET / HTTP/1.1\r\n"  # request line ends, then EOF before the blank line
    assert_raises(PrematureEOF, lambda: collect_requests(raw))


def test_parse_line_size_limit():
    raw = b"GET /" + (b"a" * 8192) + b" HTTP/1.1\r\n\r\n"
    assert_raises(LineTooLong, lambda: collect_requests(raw))


def test_parse_header_section_size_limit():
    fields = b"X: " + (b"a" * 1000) + b"\r\n"
    raw = b"GET / HTTP/1.1\r\n" + (fields * 66) + b"\r\n"
    assert_raises(HeaderSectionTooLarge, lambda: collect_requests(raw))


def test_parse_conflicting_content_length_values():
    raw = b"POST /a HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 3\r\n\r\nhi"
    assert_raises(InvalidFraming, lambda: collect_requests(raw))


# --------------------------------------------------------------------------
# Response decision branches
# --------------------------------------------------------------------------

def test_parse_response_discard_body_continues():
    raw = (
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi"
        b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
    )
    resps = collect_responses(raw, decide=lambda s, r, h: Decision.DISCARD_BODY)
    assert len(resps) == 2
    assert resps[0]["body"] is None


def test_parse_response_reject_stops_parsing():
    raw = (
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi"
        b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
    )
    resps = collect_responses(raw, decide=lambda s, r, h: Decision.REJECT)
    assert len(resps) == 1


def test_parse_response_no_body_continues():
    raw = (
        b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
    )
    resps = collect_responses(raw)
    assert [r["status_code"] for r in resps] == [204, 200]
    assert resps[0]["body"] is None


def test_parse_response_body_not_consumed_error():
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\n\r\nHello, World!"

    def on_headers(status_code, reason, headers):
        return Decision.READ_BODY

    def on_body(reader):
        reader.read(1)  # leave the rest unread

    assert_raises(
        BodyNotConsumedError,
        lambda: parse_http_1_1_responses(
            io.BytesIO(raw), on_headers=on_headers, on_body=on_body
        ),
    )


def test_parse_chunked_incremental_reads():
    # Drive the bounded-read paths: read(0), small n reads, and a read past
    # exhaustion.
    raw = (
        b"POST /a HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"5\r\nHello\r\n6\r\n World\r\n0\r\n\r\n"
    )
    collected = {}

    def on_headers(method, target, headers):
        return Decision.READ_BODY

    def on_body(reader):
        assert bytes(reader.read(0)) == b""  # zero-length read short-circuits
        buf = bytearray()
        while True:
            piece = reader.read(3)  # bounded reads
            if not piece:
                break
            buf.extend(piece)
        assert bytes(reader.read(3)) == b""  # read past exhaustion
        collected["body"] = bytes(buf)

    parse_http_1_1_requests(io.BytesIO(raw), on_headers=on_headers, on_body=on_body)
    assert collected["body"] == b"Hello World"


# --------------------------------------------------------------------------
# Miscellaneous head branches
# --------------------------------------------------------------------------

def test_parse_response_unsupported_version():
    raw = b"HTTP/1.0 200 OK\r\n\r\n"
    assert_raises(UnsupportedHTTPVersion, lambda: collect_responses(raw))


def test_parse_header_empty_value():
    raw = b"GET / HTTP/1.1\r\nX-Empty:\r\n\r\n"
    reqs = collect_requests(raw)
    assert reqs[0]["headers"] == {u"x-empty": [u""]}


def test_parse_request_non_decision_return():
    raw = b"GET / HTTP/1.1\r\nHost: h\r\n\r\n"
    assert_raises(
        TypeError,
        lambda: parse_http_1_1_requests(
            io.BytesIO(raw),
            on_headers=lambda m, t, h: None,  # not a Decision
            on_body=lambda reader: None,
        ),
    )


def test_parse_response_non_decision_return():
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
    assert_raises(
        TypeError,
        lambda: parse_http_1_1_responses(
            io.BytesIO(raw),
            on_headers=lambda s, r, h: None,  # not a Decision
            on_body=lambda reader: None,
        ),
    )


def test_parse_bare_cr_in_line():
    # A bare CR (not part of CRLF) is read as ordinary line content; the line
    # then fails to parse as a request line.
    raw = b"GET /a\r\rb HTTP/1.1\r\n\r\n"
    assert_raises(MalformedRequestLine, lambda: collect_requests(raw))


def test_parse_premature_eof_after_bare_cr():
    # Stream ends immediately after a lone CR, mid-line.
    raw = b"GET /a\r"
    assert_raises(PrematureEOF, lambda: collect_requests(raw))


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def run_all():
    # type: () -> int
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    failures = 0
    for name, func in tests:
        # A test harness legitimately catches every exception so that one
        # failing case does not hide the results of the others; the failure is
        # reported and the process exits non-zero below.
        try:
            func()
        except Exception as exc:
            failures += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
        else:
            print("ok   %s" % (name,))
    print(
        "\n%d passed, %d failed, %d total"
        % (len(tests) - failures, failures, len(tests))
    )
    return failures


if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)

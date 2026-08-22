# Copyright (c) 2026 Jifeng Wu
# Licensed under the MIT License. See LICENSE file in the project root
# for full license information.
import six
from six import text_type
from enum import Enum

from typing import (
    BinaryIO,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from lark import Lark, Token, Tree, UnexpectedInput


class Decision(Enum):
    READ_BODY = 1
    DISCARD_BODY = 2
    REJECT = 3
    ABORT = 4


class ReadState(Enum):
    DATA = 1
    CR = 2


class BodyReader(object):
    __slots__ = ()

    def read(self, n=-1):
        # type: (int) -> Union[bytes, bytearray]
        raise NotImplementedError()

    def is_exhausted(self):
        # type: () -> bool
        raise NotImplementedError()


class ParserError(Exception):
    """Base class for protocol-level parser failures."""

    __slots__ = ()


class MalformedRequestLine(ParserError):
    __slots__ = ()


class MalformedStatusLine(ParserError):
    __slots__ = ()


class MalformedHeader(ParserError):
    __slots__ = ()


class UnsupportedHTTPVersion(ParserError):
    __slots__ = ()


class InvalidFraming(ParserError):
    __slots__ = ()


class UnsupportedTransferEncoding(ParserError):
    __slots__ = ()


class PrematureEOF(ParserError):
    __slots__ = ()


class BodyNotConsumedError(ParserError):
    __slots__ = ()


class LineTooLong(ParserError):
    __slots__ = ()


class HeaderSectionTooLarge(ParserError):
    __slots__ = ()


class ParserState(Enum):
    REQUEST_LINE = 1
    STATUS_LINE = 2
    HEADERS = 3
    DECISION = 4
    BODY_READ = 5
    BODY_DISCARD = 6
    DONE = 7


class ChunkedState(Enum):
    SIZE = 1
    DATA = 2
    DATA_CRLF = 3
    TRAILERS = 4
    DONE = 5


class BodyKind(Enum):
    NONE = 1
    CONTENT_LENGTH = 2
    CHUNKED = 3
    CLOSE_DELIMITED = 4


class RequestLine(object):
    __slots__ = ("method", "target", "version")

    def __init__(self, method, target, version):
        # type: (text_type, text_type, text_type) -> None
        self.method = method
        self.target = target
        self.version = version


class StatusLine(object):
    __slots__ = ("version", "status_code", "reason")

    def __init__(self, version, status_code, reason):
        # type: (text_type, int, text_type) -> None
        self.version = version
        self.status_code = status_code
        self.reason = reason


class HeaderField(object):
    __slots__ = ("name", "value")

    def __init__(self, name, value):
        # type: (text_type, text_type) -> None
        self.name = name
        self.value = value


class ChunkHeader(object):
    __slots__ = ("size",)

    def __init__(self, size):
        # type: (int) -> None
        self.size = size


class RequestHead(object):
    __slots__ = ("method", "target", "headers", "body_kind", "body_length")

    def __init__(self, method, target, headers, body_kind, body_length=0):
        # type: (text_type, text_type, Dict[text_type, List[text_type]], BodyKind, int) -> None
        self.method = method
        self.target = target
        self.headers = headers
        self.body_kind = body_kind
        self.body_length = body_length


class ResponseHead(object):
    __slots__ = (
        "status_code",
        "reason",
        "headers",
        "body_kind",
        "body_length",
        "request_method",
    )

    def __init__(
        self,
        status_code,
        reason,
        headers,
        body_kind,
        body_length=0,
        request_method=None,
    ):
        # type: (int, text_type, Dict[text_type, List[text_type]], BodyKind, int, Optional[text_type]) -> None
        self.status_code = status_code
        self.reason = reason
        self.headers = headers
        self.body_kind = body_kind
        self.body_length = body_length
        self.request_method = request_method


HTTP_GRAMMAR = r"""
    request_line: METHOD SP REQUEST_TARGET SP HTTP_VERSION CRLF
    status_line: HTTP_VERSION SP STATUS_CODE SP REASON_PHRASE? CRLF
    header_field: FIELD_NAME ":" HEADER_TAIL? CRLF
    transfer_encoding_value: ows TRANSFER_CODING (ows "," ows TRANSFER_CODING)* ows
    content_length_value: ows DIGITS (ows "," ows DIGITS)* ows

    ows: WS*

    METHOD: /[!#$%&'*+\-.^_`|~0-9A-Za-z]+/
    FIELD_NAME: /[!#$%&'*+\-.^_`|~0-9A-Za-z]+/
    TRANSFER_CODING: /[!#$%&'*+\-.^_`|~0-9A-Za-z]+/
    REQUEST_TARGET: /[^\x00-\x20\x7f]+/
    HTTP_VERSION: /HTTP\/[0-9]\.[0-9]/
    HEADER_TAIL: /[\t\x20-\x7e\x80-\xff]+/
    STATUS_CODE: /[0-9]{3}/
    REASON_PHRASE: /[\t\x20-\x7e\x80-\xff]+/
    DIGITS: /[0-9]+/
    CRLF: "\r\n"
    SP: " "
    WS: /[ \t]+/
"""


GRAMMAR_PARSER = Lark(
    HTTP_GRAMMAR,
    parser="lalr",
    start=[
        "request_line",
        "status_line",
        "header_field",
        "transfer_encoding_value",
        "content_length_value",
    ],
    maybe_placeholders=False,
)


MAX_LINE_SIZE = 8192
MAX_HEADER_SECTION_SIZE = 65536


def read_cr_lf_terminated_line(stream, max_size=MAX_LINE_SIZE):
    # type: (BinaryIO, int) -> Tuple[bool, bytearray]
    result = bytearray()
    state = ReadState.DATA

    while True:
        chunk = stream.read(1)

        if state is ReadState.DATA:
            if not chunk:
                return False, result
            elif chunk == b"\r":
                result.extend(chunk)
                state = ReadState.CR
            else:
                result.extend(chunk)
        else:
            if not chunk:
                return False, result
            elif chunk == b"\n":
                result.extend(chunk)
                if len(result) > max_size:
                    raise LineTooLong("HTTP line exceeds %d bytes" % (max_size,))
                return True, result
            elif chunk == b"\r":
                result.extend(chunk)
            else:
                result.extend(chunk)
                state = ReadState.DATA

        if len(result) > max_size:
            raise LineTooLong("HTTP line exceeds %d bytes" % (max_size,))


def read_exact(stream, n):
    # type: (BinaryIO, int) -> bytearray
    if n < 0:
        raise ValueError("n must be non-negative")

    out = bytearray()
    while len(out) < n:
        chunk = stream.read(n - len(out))
        if not chunk:
            break
        out.extend(chunk)
    return out


def read_line(stream, eof_message, max_size=MAX_LINE_SIZE):
    # type: (BinaryIO, str, int) -> Optional[bytearray]
    complete, line = read_cr_lf_terminated_line(stream, max_size=max_size)
    if complete:
        return line
    if not line:
        return None
    raise PrematureEOF(eof_message)


def parse_grammar(data, start, error_type, message):
    # type: (Union[bytes, bytearray], str, type, str) -> Tree
    try:
        tree = GRAMMAR_PARSER.parse(data.decode("latin-1"), start=start)
    except UnexpectedInput as exc:
        six.raise_from(error_type(message), exc)
    assert isinstance(tree, Tree)
    return tree


def grammar_tokens(tree, token_type):
    # type: (Tree, str) -> List[Token]
    return [
        value
        for value in tree.scan_values(
            lambda value: isinstance(value, Token) and value.type == token_type
        )
    ]


def parse_request_line(line):
    # type: (Union[bytes, bytearray]) -> RequestLine
    tree = parse_grammar(
        line,
        start="request_line",
        error_type=MalformedRequestLine,
        message="malformed request line",
    )
    method = text_type(grammar_tokens(tree, "METHOD")[0])
    target = text_type(grammar_tokens(tree, "REQUEST_TARGET")[0])
    version = text_type(grammar_tokens(tree, "HTTP_VERSION")[0])
    if version != "HTTP/1.1":
        raise UnsupportedHTTPVersion("only HTTP/1.1 requests are supported")
    return RequestLine(method=method, target=target, version=version)


def parse_status_line(line):
    # type: (Union[bytes, bytearray]) -> StatusLine
    tree = parse_grammar(
        line,
        start="status_line",
        error_type=MalformedStatusLine,
        message="malformed status line",
    )
    version = text_type(grammar_tokens(tree, "HTTP_VERSION")[0])
    status_code = int(text_type(grammar_tokens(tree, "STATUS_CODE")[0]))
    reason_tokens = grammar_tokens(tree, "REASON_PHRASE")
    reason = text_type(reason_tokens[0]) if reason_tokens else u""
    if version != "HTTP/1.1":
        raise UnsupportedHTTPVersion("only HTTP/1.1 responses are supported")
    return StatusLine(version=version, status_code=status_code, reason=reason)


def parse_header_field(line):
    # type: (Union[bytes, bytearray]) -> HeaderField
    if line[:1] in (b" ", b"\t"):
        raise MalformedHeader("obsolete folded headers are not supported")
    tree = parse_grammar(
        line,
        start="header_field",
        error_type=MalformedHeader,
        message="malformed header line",
    )
    name = text_type(grammar_tokens(tree, "FIELD_NAME")[0]).lower()
    value_tokens = grammar_tokens(tree, "HEADER_TAIL")
    if not value_tokens:
        value = u""
    else:
        value = text_type(value_tokens[0]).strip(u" \t")
    return HeaderField(name=name, value=value)


TOKEN_BYTE_VALUES = frozenset(
    bytearray(b"!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
)
HEXADECIMAL_BYTE_VALUES = frozenset(bytearray(b"0123456789ABCDEFabcdef"))


def parse_chunk_header(line):
    # type: (Union[bytes, bytearray]) -> ChunkHeader
    data = bytearray(line)
    end = len(data) - 2
    if end < 1 or data[end:] != bytearray(b"\r\n"):
        raise InvalidFraming("invalid chunk size line")

    position = 0
    while position < end and data[position] in HEXADECIMAL_BYTE_VALUES:
        position += 1
    if position == 0:
        raise InvalidFraming("invalid chunk size line")

    size = int(bytes(data[:position]), 16)

    while position < end:
        if data[position] != ord(";"):
            raise InvalidFraming("invalid chunk extension")
        position += 1

        name_start = position
        while position < end and data[position] in TOKEN_BYTE_VALUES:
            position += 1
        if position == name_start:
            raise InvalidFraming("invalid chunk extension name")

        if position < end and data[position] == ord("="):
            position += 1
            if position >= end:
                raise InvalidFraming("missing chunk extension value")

            if data[position] == ord('"'):
                position += 1
                closed = False
                while position < end:
                    value = data[position]
                    if value == ord('"'):
                        position += 1
                        closed = True
                        break
                    if value == ord("\\"):
                        position += 1
                        if position >= end:
                            raise InvalidFraming("invalid quoted chunk extension")
                        value = data[position]
                        if not (
                            value == 9
                            or 32 <= value <= 126
                            or 128 <= value <= 255
                        ):
                            raise InvalidFraming("invalid quoted chunk extension")
                    elif not (
                        value == 9
                        or value == 32
                        or value == 33
                        or 35 <= value <= 91
                        or 93 <= value <= 126
                        or 128 <= value <= 255
                    ):
                        raise InvalidFraming("invalid quoted chunk extension")
                    position += 1
                if not closed:
                    raise InvalidFraming("unterminated quoted chunk extension")
            else:
                value_start = position
                while position < end and data[position] in TOKEN_BYTE_VALUES:
                    position += 1
                if position == value_start:
                    raise InvalidFraming("invalid chunk extension value")

    return ChunkHeader(size=size)


def parse_transfer_encoding(values):
    # type: (Sequence[text_type]) -> List[text_type]
    codings = []  # type: List[text_type]
    for raw_value in values:
        tree = parse_grammar(
            raw_value.encode("latin-1"),
            start="transfer_encoding_value",
            error_type=InvalidFraming,
            message="invalid transfer-encoding value",
        )
        codings.extend(
            text_type(token).lower()
            for token in grammar_tokens(tree, "TRANSFER_CODING")
        )
    return codings


def parse_content_length(values):
    # type: (Sequence[text_type]) -> int
    lengths = []  # type: List[int]
    for raw_value in values:
        tree = parse_grammar(
            raw_value.encode("latin-1"),
            start="content_length_value",
            error_type=InvalidFraming,
            message="invalid content-length value",
        )
        lengths.extend(
            int(text_type(token)) for token in grammar_tokens(tree, "DIGITS")
        )

    if not lengths:
        raise InvalidFraming("missing content-length value")

    first = lengths[0]
    if any(length != first for length in lengths[1:]):
        raise InvalidFraming("conflicting content-length values")
    return first


class ContentLengthBodyReader(BodyReader):
    __slots__ = ("stream", "remaining")

    def __init__(self, stream, length):
        # type: (BinaryIO, int) -> None
        self.stream = stream
        self.remaining = length

    def read(self, n=-1):
        # type: (int) -> Union[bytes, bytearray]
        if self.remaining == 0:
            return b""

        if n is None or n < 0:
            n = self.remaining
        else:
            n = min(n, self.remaining)

        data = read_exact(self.stream, n)
        if len(data) != n:
            raise PrematureEOF("unexpected EOF in request body")

        self.remaining -= len(data)
        return data

    def is_exhausted(self):
        # type: () -> bool
        return self.remaining == 0


class CloseDelimitedBodyReader(BodyReader):
    __slots__ = ("stream", "exhausted")

    def __init__(self, stream):
        # type: (BinaryIO) -> None
        self.stream = stream
        self.exhausted = False

    def read(self, n=-1):
        # type: (int) -> Union[bytes, bytearray]
        if self.exhausted:
            return b""
        if n == 0:
            return b""

        data = self.stream.read(n)
        if not data:
            self.exhausted = True
            return b""
        if n is None or n < 0:
            self.exhausted = True
        return data

    def is_exhausted(self):
        # type: () -> bool
        return self.exhausted


class ChunkedBodyReader(BodyReader):
    """Decode a chunked request body behind a simple ``read()`` API."""

    __slots__ = (
        "stream",
        "state",
        "chunk_remaining",
        "trailer_section_size",
    )

    def __init__(self, stream):
        # type: (BinaryIO) -> None
        self.stream = stream
        self.state = ChunkedState.SIZE
        self.chunk_remaining = 0
        self.trailer_section_size = 0

    def read(self, n=-1):
        # type: (int) -> Union[bytes, bytearray]
        if self.state is ChunkedState.DONE:
            return b""

        if n == 0:
            return b""

        limit = None if n is None or n < 0 else n
        out = bytearray()

        while limit is None or len(out) < limit:
            if self.state is ChunkedState.SIZE:
                self.read_chunk_header()
            elif self.state is ChunkedState.DATA:
                self.read_chunk_data_into(out, limit)
                if limit is not None and len(out) >= limit:
                    return out
            elif self.state is ChunkedState.DATA_CRLF:
                self.consume_chunk_terminator()
            elif self.state is ChunkedState.TRAILERS:
                self.consume_trailers()
                return out
            elif self.state is ChunkedState.DONE:
                return out
            else:  # pragma: no cover
                raise AssertionError(
                    "unexpected chunked state: %s" % (self.state,)
                )

        return out

    def read_chunk_header(self):
        # type: () -> None
        line = read_line(
            self.stream, eof_message="unexpected EOF while reading chunk size"
        )
        if line is None:
            raise PrematureEOF("unexpected EOF while reading chunk size")
        chunk = parse_chunk_header(line)
        self.chunk_remaining = chunk.size
        self.state = ChunkedState.TRAILERS if chunk.size == 0 else ChunkedState.DATA

    def read_chunk_data_into(self, out, limit):
        # type: (bytearray, Optional[int]) -> None
        need = self.chunk_remaining
        if limit is not None:
            need = min(need, limit - len(out))
        if need == 0:
            return

        data = read_exact(self.stream, need)
        if len(data) != need:
            raise PrematureEOF("unexpected EOF in chunk data")

        out.extend(data)
        self.chunk_remaining -= len(data)
        if self.chunk_remaining == 0:
            self.state = ChunkedState.DATA_CRLF

    def consume_chunk_terminator(self):
        # type: () -> None
        marker = read_exact(self.stream, 2)
        if marker != b"\r\n":
            if len(marker) < 2:
                raise PrematureEOF("unexpected EOF after chunk data")
            raise InvalidFraming("missing CRLF after chunk data")
        self.state = ChunkedState.SIZE

    def consume_trailers(self):
        # type: () -> None
        while True:
            line = read_line(
                self.stream,
                eof_message="unexpected EOF while reading chunked trailers",
            )
            if line is None:
                raise PrematureEOF("unexpected EOF while reading chunked trailers")
            self.trailer_section_size += len(line)
            if self.trailer_section_size > MAX_HEADER_SECTION_SIZE:
                raise HeaderSectionTooLarge(
                    "trailer section exceeds %d bytes"
                    % (MAX_HEADER_SECTION_SIZE,)
                )
            if line == b"\r\n":
                self.state = ChunkedState.DONE
                return
            parse_header_field(line)

    def is_exhausted(self):
        # type: () -> bool
        return self.state is ChunkedState.DONE


BODY_CHUNK = 65536


def make_body_reader(stream, body_kind, body_length):
    # type: (BinaryIO, BodyKind, int) -> BodyReader
    if body_kind is BodyKind.CONTENT_LENGTH:
        return ContentLengthBodyReader(stream, body_length)
    elif body_kind is BodyKind.CHUNKED:
        return ChunkedBodyReader(stream)
    elif body_kind is BodyKind.CLOSE_DELIMITED:
        return CloseDelimitedBodyReader(stream)
    else:  # pragma: no cover
        raise AssertionError("unexpected body kind: %s" % (body_kind,))


def read_headers(stream):
    # type: (BinaryIO) -> Dict[text_type, List[text_type]]
    headers = {}  # type: Dict[text_type, List[text_type]]
    section_size = 0

    while True:
        line = read_line(stream, eof_message="unexpected EOF while reading headers")
        if line is None:
            raise PrematureEOF("unexpected EOF while reading headers")
        section_size += len(line)
        if section_size > MAX_HEADER_SECTION_SIZE:
            raise HeaderSectionTooLarge(
                "header section exceeds %d bytes" % (MAX_HEADER_SECTION_SIZE,)
            )
        if line == b"\r\n":
            return headers
        field = parse_header_field(line)
        headers.setdefault(field.name, []).append(field.value)


def determine_body(headers):
    # type: (Dict[text_type, List[text_type]]) -> Tuple[BodyKind, int]
    has_te = "transfer-encoding" in headers
    has_cl = "content-length" in headers

    if has_te and has_cl:
        raise InvalidFraming(
            "transfer-encoding and content-length must not both be present"
        )

    if has_te:
        codings = parse_transfer_encoding(headers["transfer-encoding"])
        if codings != ["chunked"]:
            raise UnsupportedTransferEncoding(
                "V1 only supports a single transfer-encoding: chunked"
            )
        return BodyKind.CHUNKED, 0

    if has_cl:
        length = parse_content_length(headers["content-length"])
        if length == 0:
            return BodyKind.NONE, 0
        return BodyKind.CONTENT_LENGTH, length

    return BodyKind.NONE, 0


def determine_response_body(headers, status_code, request_method):
    # type: (Dict[text_type, List[text_type]], int, Optional[text_type]) -> Tuple[BodyKind, int]
    if request_method == u"HEAD":
        return BodyKind.NONE, 0
    if request_method == u"CONNECT" and 200 <= status_code < 300:
        return BodyKind.NONE, 0
    if 100 <= status_code < 200 or status_code == 204 or status_code == 304:
        return BodyKind.NONE, 0

    if "transfer-encoding" not in headers and "content-length" not in headers:
        return BodyKind.CLOSE_DELIMITED, 0
    return determine_body(headers)


def drain_body(reader):
    # type: (BodyReader) -> None
    while reader.read(BODY_CHUNK):
        pass


def parse_http_1_1_requests(stream, on_headers, on_body):
    # type: (BinaryIO, Callable[[text_type, text_type, Dict[text_type, List[text_type]]], Decision], Callable[[BodyReader], None]) -> None
    """Parse a stream of HTTP/1.1 requests sequentially.

    Clean EOF at a request boundary ends parsing normally.
    Protocol-level failures raise ``ParserError`` subclasses and terminate parsing.
    Callback failures from ``on_headers`` or ``on_body`` propagate as raised.
    """

    state = ParserState.REQUEST_LINE
    current = None  # type: Optional[RequestHead]
    body_reader = None  # type: Optional[BodyReader]

    while state is not ParserState.DONE:
        if state is ParserState.REQUEST_LINE:
            line = read_line(
                stream, eof_message="unexpected EOF while reading request line"
            )
            if line is None:
                state = ParserState.DONE
                continue

            request_line = parse_request_line(line)
            current = RequestHead(
                method=request_line.method,
                target=request_line.target,
                headers={},
                body_kind=BodyKind.NONE,
                body_length=0,
            )
            state = ParserState.HEADERS

        elif state is ParserState.HEADERS:
            assert current is not None
            headers = read_headers(stream)
            body_kind, body_length = determine_body(headers)
            current = RequestHead(
                method=current.method,
                target=current.target,
                headers=headers,
                body_kind=body_kind,
                body_length=body_length,
            )
            state = ParserState.DECISION

        elif state is ParserState.DECISION:
            assert current is not None
            decision = on_headers(current.method, current.target, current.headers)
            if not isinstance(decision, Decision):
                raise TypeError("on_headers() must return a Decision")

            if decision in (Decision.ABORT, Decision.REJECT):
                state = ParserState.DONE
                continue

            if current.body_kind is BodyKind.NONE:
                current = None
                body_reader = None
                state = ParserState.REQUEST_LINE
                continue

            body_reader = make_body_reader(
                stream, current.body_kind, current.body_length
            )
            state = (
                ParserState.BODY_READ
                if decision is Decision.READ_BODY
                else ParserState.BODY_DISCARD
            )

        elif state is ParserState.BODY_READ:
            assert body_reader is not None
            on_body(body_reader)
            if not body_reader.is_exhausted():
                raise BodyNotConsumedError(
                    "on_body() returned before the request body was fully consumed"
                )
            current = None
            body_reader = None
            state = ParserState.REQUEST_LINE

        elif state is ParserState.BODY_DISCARD:
            assert body_reader is not None
            drain_body(body_reader)
            current = None
            body_reader = None
            state = ParserState.REQUEST_LINE

        elif state is ParserState.DONE:
            break

        else:  # pragma: no cover
            raise AssertionError("unexpected parser state: %s" % (state,))


def parse_http_1_1_responses(
    stream, on_headers, on_body, request_methods=None
):
    # type: (BinaryIO, Callable[[int, text_type, Dict[text_type, List[text_type]]], Decision], Callable[[BodyReader], None], Optional[Sequence[text_type]]) -> None
    """Parse a stream of HTTP/1.1 responses sequentially.

    Clean EOF at a response boundary ends parsing normally.
    Protocol-level failures raise ``ParserError`` subclasses and terminate parsing.
    Callback failures from ``on_headers`` or ``on_body`` propagate as raised.
    ``request_methods`` supplies one method for each final response, allowing
    responses to HEAD and successful CONNECT requests to be framed correctly.
    """

    state = ParserState.STATUS_LINE
    current = None  # type: Optional[ResponseHead]
    body_reader = None  # type: Optional[BodyReader]
    request_index = 0

    while state is not ParserState.DONE:
        if state is ParserState.STATUS_LINE:
            line = read_line(
                stream, eof_message="unexpected EOF while reading status line"
            )
            if line is None:
                state = ParserState.DONE
                continue

            status_line = parse_status_line(line)
            request_method = None  # type: Optional[text_type]
            if request_methods is not None:
                if request_index >= len(request_methods):
                    raise ValueError(
                        "request_methods has no entry for response %d"
                        % (request_index,)
                    )
                request_method = request_methods[request_index]

            current = ResponseHead(
                status_code=status_line.status_code,
                reason=status_line.reason,
                headers={},
                body_kind=BodyKind.NONE,
                body_length=0,
                request_method=request_method,
            )
            state = ParserState.HEADERS

        elif state is ParserState.HEADERS:
            assert current is not None
            headers = read_headers(stream)
            body_kind, body_length = determine_response_body(
                headers, current.status_code, current.request_method
            )
            current = ResponseHead(
                status_code=current.status_code,
                reason=current.reason,
                headers=headers,
                body_kind=body_kind,
                body_length=body_length,
                request_method=current.request_method,
            )
            state = ParserState.DECISION

        elif state is ParserState.DECISION:
            assert current is not None
            decision = on_headers(
                current.status_code, current.reason, current.headers
            )
            if not isinstance(decision, Decision):
                raise TypeError("on_headers() must return a Decision")

            if decision in (Decision.ABORT, Decision.REJECT):
                state = ParserState.DONE
                continue

            if current.body_kind is BodyKind.NONE:
                is_tunnel = (
                    current.request_method == u"CONNECT"
                    and 200 <= current.status_code < 300
                )
                is_switch = current.status_code == 101
                if current.status_code >= 200:
                    request_index += 1
                current = None
                body_reader = None
                state = (
                    ParserState.DONE
                    if is_tunnel or is_switch
                    else ParserState.STATUS_LINE
                )
                continue

            body_reader = make_body_reader(
                stream, current.body_kind, current.body_length
            )
            state = (
                ParserState.BODY_READ
                if decision is Decision.READ_BODY
                else ParserState.BODY_DISCARD
            )

        elif state is ParserState.BODY_READ:
            assert body_reader is not None
            on_body(body_reader)
            if not body_reader.is_exhausted():
                raise BodyNotConsumedError(
                    "on_body() returned before the response body was fully consumed"
                )
            request_index += 1
            current = None
            body_reader = None
            state = ParserState.STATUS_LINE

        elif state is ParserState.BODY_DISCARD:
            assert body_reader is not None
            drain_body(body_reader)
            request_index += 1
            current = None
            body_reader = None
            state = ParserState.STATUS_LINE

        elif state is ParserState.DONE:
            break

        else:  # pragma: no cover
            raise AssertionError("unexpected parser state: %s" % (state,))

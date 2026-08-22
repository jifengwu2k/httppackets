# `httppackets`

Streaming, callback-based parsers and serializers for HTTP/1.1 requests and responses.

- **`http_1_1_parser`** — parse HTTP/1.1 requests and responses from a `BinaryIO` stream.
- **`http_1_1_serializer`** — serialize HTTP/1.1 requests and responses to a `BinaryIO` stream.

## Features

- **Streaming design** — parses and writes messages sequentially from/to any `BinaryIO` source/sink.
- **Callback-based parsing API** — separate callbacks for header decisions and body consumption.
- **Push-based serialization API** — feed structured data, get well-formed HTTP/1.1 bytes out.
- **Strict protocol checks** — rejects folded headers, conflicting framing, and unrecognised transfer codings.
- **Body framing** — supports `Content-Length`, `Transfer-Encoding: chunked`, close-delimited response bodies, and no-body messages.
- **Typed errors** — every protocol-level failure is a distinct error subclass.
- **Highly readable code** — plain, linear, imperative Python with no magic; easy to understand, audit, and port to other languages.

## Installation

```
pip install httppackets
```

## Quick start

### Parse HTTP/1.1 requests

```python
import io
from httppackets.http_1_1_parser import (
    parse_http_1_1_requests,
    Decision,
    ParserError,
)

def on_headers(method, target, headers):
    # type: (str, str, dict) -> Decision
    print("%s %s" % (method, target))
    for name, values in headers.items():
        for v in values:
            print("  %s: %s" % (name, v))

    if method == "GET":
        return Decision.READ_BODY
    if method == "POST":
        return Decision.READ_BODY
    return Decision.DISCARD_BODY

def on_body(reader):
    data = reader.read()
    print("  body: %r" % (data,))

raw = (
    b"GET /hello HTTP/1.1\r\n"
    b"Host: example.com\r\n"
    b"\r\n"
    b"POST /submit HTTP/1.1\r\n"
    b"Host: example.com\r\n"
    b"Content-Length: 13\r\n"
    b"\r\n"
    b"Hello, World!"
)

try:
    parse_http_1_1_requests(io.BytesIO(raw), on_headers=on_headers, on_body=on_body)
except ParserError as exc:
    print("Error: %s" % (exc,))
```

Output:

```
GET /hello
  host: example.com
POST /submit
  host: example.com
  content-length: 13
  body: bytearray(b'Hello, World!')
```

### Serialize HTTP/1.1 requests

```python
import io
from httppackets.http_1_1_serializer import serialize_http_1_1_request

out = io.BytesIO()

# Request with a bytes body — Content-Length is added automatically.
serialize_http_1_1_request(
    out,
    method="POST",
    target="/submit",
    headers={"host": ["example.com"], "content-type": ["text/plain"]},
    body=b"Hello, World!",
)

# Request with no body.
serialize_http_1_1_request(
    out,
    method="GET",
    target="/hello",
    headers={"host": ["example.com"]},
)

print(out.getvalue())
```

Output:

```
b'POST /submit HTTP/1.1\r\nhost: example.com\r\ncontent-type: text/plain\r\ncontent-length: 13\r\n\r\nHello, World!GET /hello HTTP/1.1\r\nhost: example.com\r\n\r\n'
```

### Streaming bodies with chunked transfer-encoding

```python
import io
from httppackets.http_1_1_serializer import serialize_http_1_1_request, SupportsRead

class ChunkedBody(SupportsRead):
    """Produce body data in chunks from an iterable."""
    __slots__ = ("chunks",)

    def __init__(self, chunks):
        # type: (list) -> None
        self.chunks = list(chunks)

    def read(self, n=-1):
        # type: (int) -> bytes
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if n >= 0 and len(chunk) > n:
            self.chunks.insert(0, chunk[n:])
            return chunk[:n]
        return chunk

out = io.BytesIO()
serialize_http_1_1_request(
    out,
    method="POST",
    target="/upload",
    headers={"host": ["example.com"]},
    body=ChunkedBody([b"chunk one\r\n", b"chunk two\r\n", b"final chunk"]),
)

print(out.getvalue())
```

Output:

```
b'POST /upload HTTP/1.1\r\nhost: example.com\r\ntransfer-encoding: chunked\r\n\r\nb\r\nchunk one\r\n\r\nb\r\nchunk two\r\n\r\nb\r\nfinal chunk\r\n0\r\n\r\n'
```

### Parse HTTP/1.1 responses

```python
import io
from httppackets.http_1_1_parser import (
    parse_http_1_1_responses,
    Decision,
    ParserError,
)

def on_headers(status_code, reason, headers):
    # type: (int, str, dict) -> Decision
    print("%d %s" % (status_code, reason))
    for name, values in headers.items():
        for v in values:
            print("  %s: %s" % (name, v))
    return Decision.READ_BODY

def on_body(reader):
    data = reader.read()
    print("  body: %r" % (data,))

raw = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Length: 13\r\n"
    b"\r\n"
    b"Hello, World!"
    b"HTTP/1.1 404 Not Found\r\n"
    b"Content-Length: 0\r\n"
    b"\r\n"
)

try:
    parse_http_1_1_responses(io.BytesIO(raw), on_headers=on_headers, on_body=on_body)
except ParserError as exc:
    print("Error: %s" % (exc,))
```

Output:

```
200 OK
  content-length: 13
  body: bytearray(b'Hello, World!')
404 Not Found
  content-length: 0
```

### Serialize HTTP/1.1 responses

```python
import io
from httppackets.http_1_1_serializer import serialize_http_1_1_response

out = io.BytesIO()

serialize_http_1_1_response(
    out,
    status_code=200,
    reason="OK",
    headers={"content-type": ["application/json"]},
    body=b'{"status":"ok"}',
)

print(out.getvalue())
```

Output:

```
b'HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 15\r\n\r\n{"status":"ok"}'
```

## API Reference

### `http_1_1_parser` — Request parsing

#### `parse_http_1_1_requests(stream, *, on_headers, on_body)`

Parse HTTP/1.1 requests from `stream` until clean EOF or a `ParserError` is raised.

| Parameter    | Type                                            | Description                                                         |
| ------------ | ----------------------------------------------- | ------------------------------------------------------------------- |
| `stream`     | `BinaryIO`                                      | Source of raw HTTP bytes (e.g. `socket.makefile("rb")`, `BytesIO`). |
| `on_headers` | `(method, target, headers) -> Decision`         | Called when headers are complete. `method`, `target` and header names/values are `str`. |
| `on_body`    | `(reader) -> None`                              | Called for requests with a body. Must drain the reader fully.       |

### `http_1_1_parser` — Response parsing

#### `parse_http_1_1_responses(stream, on_headers, on_body, request_methods=None)`

Parse HTTP/1.1 responses from `stream` until clean EOF or a `ParserError` is raised.

| Parameter         | Type                                            | Description                                                         |
| ----------------- | ----------------------------------------------- | ------------------------------------------------------------------- |
| `stream`          | `BinaryIO`                                      | Source of raw HTTP bytes.                                           |
| `on_headers`      | `(status_code, reason, headers) -> Decision`    | Called when headers are complete. `status_code` is `int`, `reason` is `str`, header names/values are `str`. |
| `on_body`         | `(reader) -> None`                              | Called for responses with a body. Must drain the reader fully.      |
| `request_methods` | `Optional[Sequence[str]]`                       | One method per final response. Supply this when parsing responses to `HEAD` or `CONNECT`; informational responses reuse the current method. |

Responses without `Content-Length` or `Transfer-Encoding` are read as close-delimited bodies. `1xx`, `204`, and `304` responses never have message bodies. When `request_methods` identifies a response to `HEAD`, framing headers describe the hypothetical GET response and no body is read. A successful `CONNECT` response and a `101 Switching Protocols` response stop HTTP parsing while leaving tunnel/protocol bytes unread.

### `http_1_1_serializer` — Request & response writing

#### `serialize_http_1_1_request(stream, method, target, headers, body=None)`

Write a single HTTP/1.1 request to `stream`.

| Parameter  | Type                                          | Description                                                               |
| ---------- | --------------------------------------------- | ------------------------------------------------------------------------- |
| `stream`   | `BinaryIO`                                    | Destination for the raw HTTP bytes.                                       |
| `method`   | `str`                                         | HTTP method (e.g. `"GET"`, `"POST"`).                                     |
| `target`   | `str`                                         | Request target (e.g. `"/path"`, `"*"`, absolute URI).                     |
| `headers`  | `Dict[str, List[str]]`                        | Header fields. Names are case-insensitive; values are joined per RFC 7230.|
| `body`     | `Optional[Union[bytes, SupportsRead]]`        | `None` for no body, `bytes` for Content-Length framing, `SupportsRead` for chunked encoding. |

> **Framing headers are managed by the serializer.** When `body` is `bytes` or `SupportsRead`, do **not** include a `Content-Length` or `Transfer-Encoding` header in `headers` (any casing) — the serializer adds the correct one and raises `ConflictingFramingError` if you set your own. Header names must be non-empty RFC 7230 tokens; a name containing forbidden characters raises `HeaderValueError`.

#### `serialize_http_1_1_response(stream, status_code, reason, headers, body=None)`

Write a single HTTP/1.1 response to `stream`.

| Parameter     | Type                                          | Description                                                               |
| ------------- | --------------------------------------------- | ------------------------------------------------------------------------- |
| `stream`      | `BinaryIO`                                    | Destination for the raw HTTP bytes.                                       |
| `status_code` | `int`                                         | 3-digit HTTP status code (e.g. `200`, `404`).                             |
| `reason`      | `str`                                         | Reason phrase (e.g. `"OK"`, `"Not Found"`).                               |
| `headers`     | `Dict[str, List[str]]`                        | Header fields.                                                            |
| `body`        | `Optional[Union[bytes, SupportsRead]]`        | `None` for no body, `bytes` for Content-Length framing, `SupportsRead` for chunked encoding. |

### Text and bytes

Header names and values, the request `method` and `target`, and the response `reason` are **text strings** — `str` on Python 3, `unicode` on Python 2.

- **Parsing** always returns text. Bytes `0x80`–`0xFF` in request targets, reason phrases, and header values are preserved by decoding as latin-1, so the same input yields the same text on Python 2 and Python 3.
- **Serialization** accepts either text or `bytes` for these fields. `bytes` is decoded as latin-1, so a given value produces byte-for-byte identical output on both Python versions.
- **Validation** rejects malformed start lines, invalid status codes, forbidden field-name characters, control characters in field values, and CRLF injection before writing any bytes.

#### `SupportsRead` (abstract base)

Users implement this to supply streaming request/response bodies to the serializer.

```python
class SupportsRead(object):
    __slots__ = ()

    def read(self, n=-1):
        # type: (int) -> Union[bytes, bytearray]
        """Return the next chunk of body data.  Return ``b""`` when exhausted."""
        raise NotImplementedError()
```

### `Decision` enum

Controls what happens after headers have been parsed (shared by both parsers).

| Value          | Meaning                                                                   |
| -------------- | ------------------------------------------------------------------------- |
| `READ_BODY`    | Read the body and pass it to `on_body`.                                   |
| `DISCARD_BODY` | Silently drain the body (useful for messages you don't care about).       |
| `REJECT`       | Stop parsing cleanly without reading the body.                            |
| `ABORT`        | Stop parsing cleanly without reading the body.                            |

> **Note:** Request bodies are framed by their own `Content-Length` or `Transfer-Encoding` headers; a request with neither has no body. Response framing additionally uses the status code and, when supplied, the corresponding entry in `request_methods`.

### Error hierarchy

All parser errors inherit from `ParserError(Exception)` (shared by both parsers):

- `MalformedRequestLine` — the request line could not be parsed.
- `MalformedStatusLine` — the response status line could not be parsed.
- `MalformedHeader` — a header line is malformed (includes folded headers).
- `UnsupportedHTTPVersion` — the version is not `HTTP/1.1`.
- `InvalidFraming` — conflicting framing, bad chunk header, missing CRLF, etc.
- `UnsupportedTransferEncoding` — a `Transfer-Encoding` other than `chunked`.
- `PrematureEOF` — stream ended before a message was complete.
- `BodyNotConsumedError` — `on_body` returned without draining the entire body.
- `LineTooLong` — a start line, header line, chunk-size line, or trailer exceeds 8,192 bytes.
- `HeaderSectionTooLarge` — a header or trailer section exceeds 65,536 bytes.

Serialization errors (all inherit from `SerializerError(Exception)`):

- `HeaderValueError` — a header name is empty or contains a non-token character, or a header value contains forbidden control characters.
- `ConflictingFramingError` — the caller set a `Content-Length` or `Transfer-Encoding` header on a body-bearing message; the serializer manages framing itself.
- `StartLineValueError` — a method, request target, status code, or reason phrase cannot be serialized as a valid HTTP/1.1 start line.

## Limitations

- **HTTP/1.1 only** — earlier or later versions are rejected during parsing.
- **Strict parsing** — obsolete constructs like line folding and bare `\n` are treated as errors.
- **No framing renegotiation** — transfer codings other than `chunked` are unsupported.
- **Trailer headers in chunked bodies** — they are parsed and validated but discarded.
- **Bounded metadata** — each HTTP line is limited to 8,192 bytes and each header or trailer section to 65,536 bytes.
- **Serialization produces chunked encoding for `SupportsRead` bodies** — Content-Length with a streaming body requires the caller to know the length ahead of time. Use `bytes` for that case.

## Running the tests

The test suite is a single self-contained script that runs unchanged on both Python 2 and Python 3 (and on POSIX and NT — it uses only in-memory streams and has no filesystem or network dependencies):

```
python tests.py
```

It exercises request and response parsing, serialization, body framing (`Content-Length`, chunked, close-delimited, and no body), line and header limits, strict chunk extensions, response request context, the full error hierarchy, non-ASCII (latin-1) round-trips, and cross-version output stability. The process prints one line per test and exits non-zero if any test fails.

## Contributing

Contributions are welcome! Please submit pull requests or open issues on the GitHub repository.

## License

This project is licensed under the [MIT License](LICENSE).

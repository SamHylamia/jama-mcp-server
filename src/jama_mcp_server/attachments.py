"""Helpers for retrieving Jama attachment metadata and file content.

py_jama_rest_client.client.JamaClient exposes attachment metadata (get_attachment)
and file upload (put_attachments_file), but has no method for downloading a file's
bytes, even though Jama's REST API supports it at `GET attachments/{id}/file`; and
no method for listing an item's attachments, even though the REST API supports that
too at `GET items/{itemId}/attachments`. We reach into JamaClient's private Core
instance to issue those GETs directly, so we reuse its already-authenticated HTTP
session instead of duplicating the OAuth flow.

Note on files embedded in rich text: an item's rich-text fields reference files by
URL, e.g. `<img src="https://<host>/attachment/21627/diagram.png">`. The numeric ID
in that URL is NOT a REST API attachment ID (it 404s against `GET attachments/{id}`),
and the file is often not an attachment of the item at all: an image pasted into the
rich-text editor is stored as a standalone file that `GET items/{itemId}/attachments`
doesn't list. Jama's REST API downloads these by URL instead, at `GET files?url=...`,
which was confirmed against a live Jamacloud instance to serve both pasted images and
real attachments referenced from rich text. `find_embedded_files` extracts those URLs
from an item, and `get_embedded_file` downloads one.

Note on Content-Type: `GET attachments/{id}/file` was observed (on a live Jamacloud
instance) to always respond with `Content-Type: application/octet-stream` and no
`Content-Disposition` header, regardless of the file's real type. We use the
attachment metadata's `mimeType`/`fileName` as the authoritative source instead,
falling back to sniffing the content's magic bytes for common image formats.
"""

import logging
import re
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import unquote, urljoin, urlparse

from py_jama_rest_client.client import APIException, ResourceNotFoundException

logger = logging.getLogger(__name__)

_CONTENT_DISPOSITION_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?')

_IMAGE_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
]


class AttachmentFile(NamedTuple):
    content: bytes
    content_type: str
    filename: str | None


class EmbeddedFile(NamedTuple):
    field: str
    url: str
    file_name: str
    kind: str  # "image" for an <img src>, "link" for an <a href>
    alt: str | None


class AttachmentInfo(NamedTuple):
    id: int
    file_name: str | None
    mime_type: str | None
    file_size: int | None


def get_attachment_file(jama_client, attachment_id: str) -> AttachmentFile:
    """
    Fetches the raw bytes of a Jama attachment, plus its MIME type and file name.

    Prefers a `get_attachment_file` method on the client itself (used by
    MockJamaClient, and any future py_jama_rest_client version that adds one),
    falling back to direct calls through the real client's private Core.
    """
    if hasattr(jama_client, "get_attachment_file"):
        return jama_client.get_attachment_file(attachment_id)

    core = _require_core(jama_client)

    meta_file_name, meta_mime_type = _fetch_attachment_metadata(core, attachment_id)

    resource_path = f"attachments/{attachment_id}/file"
    try:
        response = core.get(resource_path)
    except Exception as err:
        raise APIException(str(err))

    if response.status_code == 404:
        raise ResourceNotFoundException(
            f"Attachment {attachment_id} (or its file) was not found.",
            status_code=404,
        )
    response.raise_for_status()
    content = response.content

    content_type = (
        meta_mime_type
        or _sniff_image_content_type(content)
        or response.headers.get("Content-Type")
        or "application/octet-stream"
    )
    filename = meta_file_name or _filename_from_content_disposition(response.headers.get("Content-Disposition"))
    return AttachmentFile(content=content, content_type=content_type, filename=filename)


def list_item_attachments(jama_client, item_id: str) -> list[AttachmentInfo]:
    """
    Lists the attachments associated with a Jama item, giving each attachment's
    REST API ID (for use with get_attachment_file / get_jama_attachment_content).

    Prefers a `list_item_attachments` method on the client itself (used by
    MockJamaClient), falling back to a direct call through the real client's
    private Core.
    """
    if hasattr(jama_client, "list_item_attachments"):
        return jama_client.list_item_attachments(item_id)

    core = _require_core(jama_client)

    resource_path = f"items/{item_id}/attachments"
    page_size = 20
    start_index = 0
    total_results = None
    attachments: list[AttachmentInfo] = []

    while total_results is None or start_index < total_results:
        try:
            response = core.get(resource_path, params={"startAt": start_index, "maxResults": page_size})
        except Exception as err:
            raise APIException(str(err))
        if response.status_code == 404:
            raise ResourceNotFoundException(f"Item {item_id} was not found.", status_code=404)
        response.raise_for_status()

        payload = response.json()
        page_data = payload.get("data") or []
        for entry in page_data:
            attachments.append(
                AttachmentInfo(
                    id=entry["id"],
                    file_name=entry.get("fileName"),
                    mime_type=entry.get("mimeType"),
                    file_size=entry.get("fileSize"),
                )
            )

        if not page_data:
            break
        page_info = payload["meta"]["pageInfo"]
        total_results = page_info.get("totalResults", len(attachments))
        start_index = page_info["startIndex"] + page_size

    return attachments


def get_embedded_file(jama_client, url: str) -> AttachmentFile:
    """
    Fetches a file referenced by URL from an item's rich-text field (an <img src> or
    <a href> pointing at `/attachment/...`), whether it's a pasted image or a real
    attachment. See the module docstring.

    Prefers a `get_embedded_file` method on the client itself (used by
    MockJamaClient), falling back to a direct call through the real client's
    private Core.
    """
    if hasattr(jama_client, "get_embedded_file"):
        return jama_client.get_embedded_file(url)

    core = _require_core(jama_client)

    try:
        response = core.get("files", params={"url": url})
    except Exception as err:
        raise APIException(str(err))

    if response.status_code == 404:
        raise ResourceNotFoundException(f"No Jama file found at {url}.", status_code=404)
    response.raise_for_status()
    content = response.content

    content_type = (
        _sniff_image_content_type(content)
        or response.headers.get("Content-Type")
        or "application/octet-stream"
    )
    filename = _filename_from_content_disposition(response.headers.get("Content-Disposition")) or _file_name_from_url(url)
    return AttachmentFile(content=content, content_type=content_type, filename=filename)


def find_embedded_files(item: dict, base_url: str | None = None) -> list[EmbeddedFile]:
    """
    Lists the Jama files referenced from an item's rich-text fields, in field order,
    without duplicates. Relative URLs are resolved against base_url when given.
    """
    embedded: list[EmbeddedFile] = []
    seen_urls: set[str] = set()
    for field, value in (item.get("fields") or {}).items():
        if not isinstance(value, str) or "/attachment/" not in value:
            continue
        parser = _AttachmentRefParser()
        parser.feed(value)
        for kind, url, alt in parser.refs:
            if base_url:
                url = urljoin(base_url.rstrip("/") + "/", url)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            embedded.append(EmbeddedFile(field=field, url=url, file_name=_file_name_from_url(url), kind=kind, alt=alt))
    return embedded


class _AttachmentRefParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[tuple[str, str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "img":
            kind, url = "image", attributes.get("src")
        elif tag == "a":
            kind, url = "link", attributes.get("href")
        else:
            return
        if url and "/attachment/" in urlparse(url).path:
            self.refs.append((kind, url, attributes.get("alt") or None))


def _file_name_from_url(url: str) -> str:
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


def _require_core(jama_client):
    core = getattr(jama_client, "_JamaClient__core", None)
    if core is None:
        raise APIException(
            "Unable to access the underlying Jama HTTP client; "
            "py_jama_rest_client internals may have changed."
        )
    return core


def _fetch_attachment_metadata(core, attachment_id: str) -> tuple[str | None, str | None]:
    try:
        response = core.get(f"attachments/{attachment_id}")
    except Exception as err:
        raise APIException(str(err))
    if response.status_code == 404:
        raise ResourceNotFoundException(f"Attachment {attachment_id} was not found.", status_code=404)
    response.raise_for_status()
    data = response.json().get("data") or {}
    return data.get("fileName"), data.get("mimeType")


def _sniff_image_content_type(content: bytes) -> str | None:
    for signature, mime_type in _IMAGE_MAGIC_SIGNATURES:
        if content.startswith(signature):
            return mime_type
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    match = _CONTENT_DISPOSITION_FILENAME_RE.search(header)
    return match.group(1) if match else None

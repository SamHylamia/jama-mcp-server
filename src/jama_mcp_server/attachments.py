"""Helpers for retrieving Jama attachment metadata and file content.

py_jama_rest_client.client.JamaClient exposes attachment metadata (get_attachment)
and file upload (put_attachments_file), but has no method for downloading a file's
bytes, even though Jama's REST API supports it at `GET attachments/{id}/file`; and
no method for listing an item's attachments, even though the REST API supports that
too at `GET items/{itemId}/attachments`. We reach into JamaClient's private Core
instance to issue those GETs directly, so we reuse its already-authenticated HTTP
session instead of duplicating the OAuth flow.

Note on IDs: the numeric ID embedded in an attachment's web UI URL (e.g. the
`21627` in `https://<host>/attachment/21627/diagram.png`) is NOT the REST API
attachment ID, it's served by a separate, browser-session-authenticated download
endpoint with its own ID space (confirmed against a live Jamacloud instance: that
URL's ID 404s against `GET attachments/{id}`). The REST attachment ID must be
looked up per-item via `list_item_attachments`, matching by file name if needed.

Note on Content-Type: `GET attachments/{id}/file` was observed (on a live Jamacloud
instance) to always respond with `Content-Type: application/octet-stream` and no
`Content-Disposition` header, regardless of the file's real type. We use the
attachment metadata's `mimeType`/`fileName` as the authoritative source instead,
falling back to sniffing the content's magic bytes for common image formats.
"""

import logging
import re
from typing import NamedTuple

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


def resolve_attachment_id(jama_client, item_id: str, file_name: str) -> str:
    """
    Resolves an attachment's file name (e.g. from an item's <img> src, which is
    the file name but not a usable ID - see the module docstring) to its REST API
    attachment ID, by listing the item's attachments and matching by file name.
    """
    attachments = list_item_attachments(jama_client, item_id)
    matches = [a for a in attachments if a.file_name == file_name]

    if not matches:
        available = ", ".join(repr(a.file_name) for a in attachments) or "none"
        raise ResourceNotFoundException(
            f"No attachment named {file_name!r} found on item {item_id}. "
            f"Attachments on this item: {available}",
            status_code=404,
        )
    if len(matches) > 1:
        ids = ", ".join(str(a.id) for a in matches)
        raise APIException(
            f"Item {item_id} has {len(matches)} attachments named {file_name!r} "
            f"(ids: {ids}). Call get_jama_item_attachments and pass a specific "
            f"attachment_id to disambiguate."
        )
    return str(matches[0].id)


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

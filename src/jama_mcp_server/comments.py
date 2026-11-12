"""Helpers for retrieving the comments on a Jama item.

py_jama_rest_client.client.JamaClient has no comment methods, although Jama's REST
API lists an item's comments at `GET items/{itemId}/comments`. As in attachments.py,
we issue that GET through JamaClient's private Core to reuse its authenticated
session.

Each comment carries `inReplyTo` (the ID of the comment it answers), so the flat
list is grouped here into threads. Authors are user IDs; requesting
`include=data.createdBy` makes Jama return the users under `linked.users` in the
same response, so names cost no extra request.
"""

import logging

from py_jama_rest_client.client import APIException, ResourceNotFoundException

from .attachments import _require_core, find_embedded_files

logger = logging.getLogger(__name__)

_PAGE_SIZE = 50


def list_item_comments(jama_client, item_id: str) -> tuple[list[dict], dict]:
    """
    Returns an item's raw comments (in Jama's order) and the linked users keyed by
    user ID as a string.

    Prefers a `list_item_comments` method on the client itself (used by
    MockJamaClient), falling back to direct calls through the real client's private
    Core.
    """
    if hasattr(jama_client, "list_item_comments"):
        return jama_client.list_item_comments(item_id)

    core = _require_core(jama_client)
    resource_path = f"items/{item_id}/comments"
    comments: list[dict] = []
    users: dict = {}
    start_index = 0
    total_results = None

    while total_results is None or start_index < total_results:
        params = {"startAt": start_index, "maxResults": _PAGE_SIZE, "include": "data.createdBy"}
        try:
            response = core.get(resource_path, params=params)
        except Exception as err:
            raise APIException(str(err))
        if response.status_code == 404:
            raise ResourceNotFoundException(f"Item {item_id} was not found.", status_code=404)
        response.raise_for_status()

        payload = response.json()
        page_data = payload.get("data") or []
        comments.extend(page_data)
        users.update(((payload.get("linked") or {}).get("users")) or {})

        if not page_data:
            break
        page_info = payload["meta"]["pageInfo"]
        total_results = page_info.get("totalResults", len(comments))
        start_index = page_info["startIndex"] + _PAGE_SIZE

    return comments, users


def build_comment_threads(comments: list[dict], users: dict, base_url: str | None = None) -> list[dict]:
    """
    Groups comments into threads: each root comment gets a `replies` list holding
    every comment beneath it (replies to replies included), oldest first. A reply
    whose parent isn't in the list becomes a root itself. Deleted comments are
    dropped.
    """
    comments = [c for c in comments if not c.get("deleted")]
    by_id = {comment.get("id"): comment for comment in comments}

    def root_of(comment: dict) -> dict:
        seen = set()
        while comment.get("inReplyTo") in by_id and comment["id"] not in seen:
            seen.add(comment["id"])
            comment = by_id[comment["inReplyTo"]]
        return comment

    threads: dict = {}
    order: list = []
    for comment in sorted(comments, key=lambda c: c.get("createdDate") or ""):
        root = root_of(comment)
        if root["id"] not in threads:
            threads[root["id"]] = {**_summarize(root, users, base_url), "replies": []}
            order.append(root["id"])
        if comment is not root:
            threads[root["id"]]["replies"].append(_summarize(comment, users, base_url))
    return [threads[root_id] for root_id in order]


def _summarize(comment: dict, users: dict, base_url: str | None) -> dict:
    body = comment.get("body")
    text = body.get("text") if isinstance(body, dict) else body
    summary = {
        "id": comment.get("id"),
        "author": _user_name(comment.get("createdBy"), users),
        "created_date": comment.get("createdDate"),
        "comment_type": comment.get("commentType"),
        "status": comment.get("status"),
        "body": text,
    }
    if comment.get("inReplyTo") is not None:
        summary["in_reply_to"] = comment["inReplyTo"]
    embedded = find_embedded_files({"fields": {"body": text}}, base_url=base_url)
    if embedded:
        summary["embedded_files"] = [{k: v for k, v in f._asdict().items() if k != "field"} for f in embedded]
    return summary


def _user_name(user_id, users: dict) -> str | None:
    if user_id is None:
        return None
    user = users.get(str(user_id)) or users.get(user_id)
    if not user:
        return str(user_id)
    name = " ".join(part for part in (user.get("firstName"), user.get("lastName")) if part)
    return name or user.get("username") or str(user_id)

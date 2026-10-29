import base64
import os
from urllib.parse import urljoin
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP, Context, Image
from mcp.types import BlobResourceContents, CallToolResult, EmbeddedResource, TextContent
from pydantic import AnyUrl, Field
import pydantic_core

from .attachments import (
    AttachmentFile,
    find_embedded_files,
    get_attachment_file,
    get_embedded_file,
    list_item_attachments,
)
from .auth import get_jama_credentials, CredentialsError

# Configure basic logging FIRST
logging.basicConfig(level=logging.INFO, format='%(asctime)s - SERVER - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Check if we need the real client or can use a mock
MOCK_MODE = os.environ.get("JAMA_MOCK_MODE", "false").lower() == "true"

if MOCK_MODE:
    from .mock_client import MockJamaClient as JamaClient
    logger.info("Using MockJamaClient due to JAMA_MOCK_MODE=true")
else:
    try:
        from py_jama_rest_client.client import JamaClient # Import the real client
        logger.info("Using real py_jama_rest_client.client.JamaClient")
    except ImportError:
        logger.error("Failed to import real JamaClient. Is py-jama-rest-client installed?")
        # Exit or raise a more specific error if the real client is mandatory when not in mock mode
        raise

@asynccontextmanager
async def jama_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """
    Manages the JamaClient lifecycle, retrieving credentials using get_jama_credentials
    and instantiating the JamaClient.
    """
    if MOCK_MODE:
        logger.info("Jama Mock Mode enabled. Skipping real authentication.")
        try:
            mock_client = JamaClient() # Instantiate the mock client
            yield {"jama_client": mock_client}
        except Exception as e:
             logger.error(f"Failed to initialize MockJamaClient: {e}")
             raise
        finally:
             logger.info("Jama mock lifespan context manager exiting.")
        return # Exit the function here for mock mode

    # --- Real Authentication Logic (only runs if not MOCK_MODE) ---
    jama_url = os.environ.get("JAMA_URL")
    if not jama_url:
        logger.error("JAMA_URL environment variable not set. Cannot connect to Jama.")
        # Let the lifespan fail, preventing server start without URL
        raise ValueError("JAMA_URL environment variable is required.")

    jama_client = None
    try:
        # Get credentials using the dedicated function
        logger.info("Attempting to retrieve Jama credentials...")
        client_id, client_secret = get_jama_credentials() # This handles AWS/Env Var logic and raises errors
        logger.info("Successfully retrieved Jama credentials.")

        # Instantiate the client
        logger.info(f"Attempting OAuth authentication to Jama at {jama_url}")
        jama_client = JamaClient(host_domain=jama_url, credentials=(client_id, client_secret), oauth=True)
        logger.info(f"Successfully configured JamaClient.")


        yield {"jama_client": jama_client}

    except CredentialsError as e: # Catch specific credential errors from auth.py
        logger.error(f"Failed to obtain Jama credentials: {e}")
        raise # Re-raise to prevent server start
    except Exception as e: # Catch other potential errors (e.g., JamaClient init)
        logger.error(f"Failed during JamaClient initialization or credential retrieval: {e}")
        # Re-raise the exception to prevent the server from starting incorrectly
        raise
    finally:
        # No explicit cleanup needed for JamaClient based on its usage pattern
        logger.info("Jama lifespan context manager exiting.")


# Instantiate the FastMCP server with the lifespan manager
mcp = FastMCP(
    "Jama Connect Server",
    lifespan=jama_lifespan,
)

# --- Tool Implementations ---

ItemId = Annotated[str, Field(description='Numeric item ID or documentKey (e.g. "ABC-REQ-12").')]

def _resolve_item_id(jama_client, item_id: str) -> str:
    """
    Returns the numeric API ID for `item_id`, which may be a numeric ID or a
    documentKey (e.g. "ABC-REQ-12"). Document keys are looked up via
    `GET abstractitems?documentKey=`, which matches case-insensitively and also
    returns attachments, test plans etc.; only items (`type == "items"`) count.
    """
    item_id = item_id.strip()
    if item_id.isdigit():
        return item_id
    matches = [
        item for item in jama_client.get_abstract_items(document_key=[item_id]) or []
        if item.get("type") == "items" and str(item.get("documentKey", "")).lower() == item_id.lower()
    ]
    if not matches:
        raise ValueError(f"No Jama item with ID or documentKey '{item_id}'.")
    return str(matches[0]["id"])

@mcp.tool()
async def get_jama_projects(ctx: Context) -> list[dict]:
    """
    Lists all Jama projects.
    """
    logger.info("Executing get_jama_projects tool")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    # Let exceptions from the client propagate
    projects = jama_client.get_projects()
    return projects

# Limits on images inlined by get_jama_item(include_images=True), so an item with
# many or large screenshots doesn't flood the agent's context.
MAX_INLINE_IMAGES = 10
MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024

@mcp.tool()
async def get_jama_item(item_id: ItemId, ctx: Context, include_images: bool = False) -> dict:
    """
    Gets a Jama item by its numeric ID or documentKey (e.g. "ABC-REQ-12").

    Rich-text fields (e.g. `description`) are HTML; links like
    `perspective.req?docId=<n>` point to item <n>. Custom field keys end in
    `$<itemTypeId>`; pick-list fields hold option IDs (resolve with
    get_jama_pick_list_option).

    The result adds `embedded_files`: files referenced from the rich text, each with
    `field`, `url`, `file_name`, `kind` ("image" or "link") and `alt`. Fetch them with
    get_jama_attachment_content(urls=[...]).

    Args:
        include_images: Also return the embedded images inline (max 10, 5 MB each).
            Costly in context; only set when you need to see them.
    """
    logger.info(f"Executing get_jama_item tool for item_id: {item_id}, include_images: {include_images}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    item_id = _resolve_item_id(jama_client, item_id)
    item = jama_client.get_item(item_id)
    # Let the client raise ResourceNotFoundException if applicable
    if not item and MOCK_MODE: # Handle mock case explicitly if needed
        raise ValueError(f"Mock Item with ID {item_id} not found.")
    if not item:
        return item

    embedded_files = find_embedded_files(item, base_url=os.environ.get("JAMA_URL"))
    item = {**item, "embedded_files": [f._asdict() for f in embedded_files]}
    if not include_images:
        return item

    content = [TextContent(type="text", text=pydantic_core.to_json(item, indent=2).decode())]
    for index, embedded in enumerate(f for f in embedded_files if f.kind == "image"):
        if index >= MAX_INLINE_IMAGES:
            content.append(_text(f"Not inlined (over the {MAX_INLINE_IMAGES}-image limit): {embedded.url}"))
            continue
        try:
            file = get_embedded_file(jama_client, embedded.url)
        except Exception as err:
            content.append(_text(f"Failed to fetch {embedded.url}: {err}"))
            continue
        if len(file.content) > MAX_INLINE_IMAGE_BYTES:
            content.append(_text(f"Not inlined (larger than {MAX_INLINE_IMAGE_BYTES} bytes): {embedded.url}"))
            continue
        content.append(_text(f"Image from {embedded.field}: {embedded.url}"))
        content.extend(_file_content(file, embedded.url))
    return CallToolResult(content=content, structuredContent=item)

@mcp.tool()
async def get_jama_project_items(project_id: str, ctx: Context) -> list[dict]:
    """
    Lists every item in a project. Can be very large; prefer get_jama_item_children
    or get_jama_tagged_items to narrow down.
    """
    logger.info(f"Executing get_jama_project_items tool for project_id: {project_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    items = jama_client.get_items(project_id=project_id)
    return items if items else []

@mcp.tool()
async def get_jama_item_children(item_id: ItemId, ctx: Context) -> list[dict]:
    """
    Lists an item's direct children in the project tree.
    """
    logger.info(f"Executing get_jama_item_children tool for parent_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    item_id = _resolve_item_id(jama_client, item_id)
    children = jama_client.get_item_children(item_id=item_id)
    return children if children else []

@mcp.tool()
async def get_jama_relationships(project_id: str, ctx: Context) -> list[dict]:
    """
    Lists every relationship in a project. Can be very large; prefer the per-item
    relationship tools.
    """
    logger.info(f"Executing get_jama_relationships tool for project_id: {project_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    relationships = jama_client.get_relationships(project_id=project_id)
    return relationships if relationships else []

@mcp.tool()
async def get_jama_relationship(relationship_id: str, ctx: Context) -> dict:
    """
    Gets a relationship by ID: `fromItem`, `toItem`, `relationshipType`, `suspect`.
    """
    logger.info(f"Executing get_jama_relationship tool for relationship_id: {relationship_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    relationship = jama_client.get_relationship(relationship_id=relationship_id)
    # Let py-jama-rest-client raise ResourceNotFoundException if applicable
    if not relationship and MOCK_MODE: # Handle mock case explicitly if needed
         raise ValueError(f"Mock Relationship with ID {relationship_id} not found.")
    return relationship

@mcp.tool()
async def get_jama_item_upstream_relationships(item_id: ItemId, ctx: Context) -> list[dict]:
    """
    Lists relationship records pointing into an item (`fromItem` is upstream). Use
    get_jama_item_upstream_related to get the items themselves.
    """
    logger.info(f"Executing get_jama_item_upstream_relationships tool for item_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    item_id = _resolve_item_id(jama_client, item_id)
    relationships = jama_client.get_items_upstream_relationships(item_id=item_id)
    return relationships if relationships else []

@mcp.tool()
async def get_jama_item_downstream_relationships(item_id: ItemId, ctx: Context) -> list[dict]:
    """
    Lists relationship records going out from an item (`toItem` is downstream). Use
    get_jama_item_downstream_related to get the items themselves.
    """
    logger.info(f"Executing get_jama_item_downstream_relationships tool for item_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    item_id = _resolve_item_id(jama_client, item_id)
    relationships = jama_client.get_items_downstream_relationships(item_id=item_id)
    return relationships if relationships else []

@mcp.tool()
async def get_jama_item_upstream_related(item_id: ItemId, ctx: Context) -> list[dict]:
    """
    Lists the items upstream of an item (e.g. the requirements it satisfies).
    """
    logger.info(f"Executing get_jama_item_upstream_related tool for item_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    item_id = _resolve_item_id(jama_client, item_id)
    items = jama_client.get_items_upstream_related(item_id=item_id)
    return items if items else []

@mcp.tool()
async def get_jama_item_downstream_related(item_id: ItemId, ctx: Context) -> list[dict]:
    """
    Lists the items downstream of an item (e.g. those that satisfy or verify it).
    """
    logger.info(f"Executing get_jama_item_downstream_related tool for item_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    item_id = _resolve_item_id(jama_client, item_id)
    items = jama_client.get_items_downstream_related(item_id=item_id)
    return items if items else []

@mcp.tool()
async def get_jama_item_attachments(item_id: ItemId, ctx: Context) -> list[dict]:
    """
    Lists files in an item's Attachments section: `id`, `file_name`, `mime_type`,
    `file_size`. Fetch them with get_jama_attachment_content(attachment_ids=[...]).
    Images pasted into rich text aren't listed here; see get_jama_item's
    `embedded_files`.
    """
    logger.info(f"Executing get_jama_item_attachments tool for item_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    item_id = _resolve_item_id(jama_client, item_id)
    attachments = list_item_attachments(jama_client, item_id)
    return [a._asdict() for a in attachments]

@mcp.tool()
async def get_jama_attachment_content(
    ctx: Context,
    urls: list[str] | None = None,
    attachment_ids: list[str] | None = None,
):
    """
    Fetches the content of one or more Jama files in one call. Images come back
    inline; other files as base64 embedded resources. A file that fails yields an
    error line without failing the rest.

    Args:
        urls: URLs from get_jama_item's `embedded_files` (or any rich-text
            `/attachment/...` src/href).
        attachment_ids: IDs from get_jama_item_attachments. The number in an
            `/attachment/<n>/` URL is not an attachment ID.
    """
    logger.info(f"Executing get_jama_attachment_content tool for urls={urls}, attachment_ids={attachment_ids}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]

    if not urls and not attachment_ids:
        raise ValueError("Provide urls and/or attachment_ids.")

    content = []
    base_url = os.environ.get("JAMA_URL")
    for url in urls or []:
        if base_url:
            url = urljoin(base_url.rstrip("/") + "/", url)
        _append_fetched(content, url, lambda: get_embedded_file(jama_client, url))
    for attachment_id in attachment_ids or []:
        uri = f"jama://attachments/{attachment_id}/file"
        _append_fetched(content, uri, lambda: get_attachment_file(jama_client, attachment_id))
    return content

def _append_fetched(content: list, uri: str, fetch) -> None:
    """Appends a fetched file, preceded by a line naming it, or a line with the fetch error."""
    try:
        file = fetch()
    except Exception as err:
        content.append(_text(f"Failed to fetch {uri}: {err}"))
        return
    content.append(_text(f"{file.filename or uri} ({file.content_type}, {len(file.content)} bytes): {uri}"))
    content.extend(_file_content(file, uri))

def _text(text: str) -> TextContent:
    return TextContent(type="text", text=text)

def _file_content(file: AttachmentFile, uri: str) -> list:
    """Converts a fetched file to MCP content: an inline image, or a base64 embedded resource."""
    if file.content_type.startswith("image/"):
        image_format = file.content_type.split("/", 1)[1].split(";")[0]
        return [Image(data=file.content, format=image_format).to_image_content()]
    return [
        EmbeddedResource(
            type="resource",
            resource=BlobResourceContents(
                uri=AnyUrl(uri),
                mimeType=file.content_type,
                blob=base64.b64encode(file.content).decode("ascii"),
            ),
        )
    ]

@mcp.tool()
async def get_jama_item_types(ctx: Context) -> list[dict]:
    """
    Lists all item types (an item's `itemType` is one of these IDs).
    """
    logger.info("Executing get_jama_item_types tool")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    item_types = jama_client.get_item_types()
    return item_types if item_types else []

@mcp.tool()
async def get_jama_item_type(item_type_id: str, ctx: Context) -> dict:
    """
    Gets an item type by ID, including its field definitions.
    """
    logger.info(f"Executing get_jama_item_type tool for item_type_id: {item_type_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    item_type = jama_client.get_item_type(item_type_id=item_type_id)
    if not item_type and MOCK_MODE:
         raise ValueError(f"Mock Item type with ID {item_type_id} not found.")
    return item_type

@mcp.tool()
async def get_jama_pick_lists(ctx: Context) -> list[dict]:
    """
    Lists all pick lists.
    """
    logger.info("Executing get_jama_pick_lists tool")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    pick_lists = jama_client.get_pick_lists()
    return pick_lists if pick_lists else []

@mcp.tool()
async def get_jama_pick_list(pick_list_id: str, ctx: Context) -> dict:
    """
    Gets a pick list by ID.
    """
    logger.info(f"Executing get_jama_pick_list tool for pick_list_id: {pick_list_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    pick_list = jama_client.get_pick_list(pick_list_id=pick_list_id)
    if not pick_list and MOCK_MODE:
         raise ValueError(f"Mock Pick list with ID {pick_list_id} not found.")
    return pick_list

@mcp.tool()
async def get_jama_pick_list_options(pick_list_id: str, ctx: Context) -> list[dict]:
    """
    Lists a pick list's options.
    """
    logger.info(f"Executing get_jama_pick_list_options tool for pick_list_id: {pick_list_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    options = jama_client.get_pick_list_options(pick_list_id=pick_list_id)
    return options if options else []

@mcp.tool()
async def get_jama_pick_list_option(pick_list_option_id: str, ctx: Context) -> dict:
    """
    Gets a pick list option by ID, e.g. to resolve an item's pick-list field value to its name.
    """
    logger.info(f"Executing get_jama_pick_list_option tool for pick_list_option_id: {pick_list_option_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    option = jama_client.get_pick_list_option(pick_list_option_id=pick_list_option_id)
    if not option and MOCK_MODE:
         raise ValueError(f"Mock Pick list option with ID {pick_list_option_id} not found.")
    return option

@mcp.tool()
async def get_jama_tags(project_id: str, ctx: Context) -> list[dict]:
    """
    Lists a project's tags.
    """
    logger.info(f"Executing get_jama_tags tool for project_id: {project_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    tags = jama_client.get_tags(project=project_id) # Param name is 'project' in client
    return tags if tags else []

@mcp.tool()
async def get_jama_tagged_items(tag_id: str, ctx: Context) -> list[dict]:
    """
    Lists the items that have a tag.
    """
    logger.info(f"Executing get_jama_tagged_items tool for tag_id: {tag_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    items = jama_client.get_tagged_items(tag_id=tag_id)
    return items if items else []

@mcp.tool()
async def get_jama_test_cycle(test_cycle_id: str, ctx: Context) -> dict:
    """
    Gets a test cycle by ID.
    """
    logger.info(f"Executing get_jama_test_cycle tool for test_cycle_id: {test_cycle_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    cycle = jama_client.get_test_cycle(test_cycle_id=test_cycle_id)
    if not cycle and MOCK_MODE:
         raise ValueError(f"Mock Test cycle with ID {test_cycle_id} not found.")
    return cycle

@mcp.tool()
async def get_jama_test_runs(test_cycle_id: str, ctx: Context) -> list[dict]:
    """
    Lists a test cycle's test runs.
    """
    logger.info(f"Executing get_jama_test_runs tool for test_cycle_id: {test_cycle_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    runs = jama_client.get_testruns(test_cycle_id=test_cycle_id)
    return runs if runs else []



@mcp.tool()
async def test_jama_connection(ctx: Context) -> dict:
    """
    Checks that the server can reach and authenticate to Jama.
    """
    logger.info("Executing test_jama_connection tool")
    jama_client: JamaClient = ctx.request_context.lifespan_context.get("jama_client")
    if not jama_client:
        raise ValueError("JamaClient not found in context for connection test.")

    # Attempt a simple API call to verify connection further
    # Let any exceptions propagate
    endpoints = jama_client.get_available_endpoints()
    return endpoints # Return the actual result or let exception indicate failure


def main():
    """Entry point for the jama-mcp-server script."""
    logger.info("Starting Jama MCP server...")

    if not MOCK_MODE and not os.environ.get("JAMA_URL"):
        logger.error("JAMA_URL environment variable is not set when not in MOCK_MODE.")
        print("\nERROR: JAMA_URL environment variable is not set.")
        print("Please set JAMA_URL and OAuth authentication variables (JAMA_CLIENT_ID, JAMA_CLIENT_SECRET),")
        print("or run in mock mode by setting JAMA_MOCK_MODE=true.")
        exit(1) # Exit if essential config is missing for non-mock mode

    # Run the MCP server (uses uvicorn defaults)
    mcp.run()

if __name__ == "__main__":
    # This allows running the server directly with `python -m jama_mcp_server.server`
    main()

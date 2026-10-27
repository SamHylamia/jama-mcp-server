import base64
import os
from urllib.parse import urljoin
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging

from mcp.server.fastmcp import FastMCP, Context, Image
from mcp.types import BlobResourceContents, CallToolResult, EmbeddedResource, TextContent
from pydantic import AnyUrl
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

@mcp.tool()
async def get_jama_projects(ctx: Context) -> list[dict]:
    """
    Retrieves a list of projects from Jama Connect.

    Returns:
        A list of dictionaries representing projects.

    Raises:
        APIException: If an error occurs during the Jama API call.
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
async def get_jama_item(item_id: str, ctx: Context, include_images: bool = False) -> dict:
    """
    Retrieves details for a specific item from Jama Connect by its ID.

    The returned item has an extra `embedded_files` list: every file its rich-text
    fields reference (images pasted into or attached to the description, linked
    files), each with `field`, `url`, `file_name`, `kind` ("image" or "link") and
    `alt`. Pass those URLs to get_jama_attachment_content to fetch them, all in
    one call. Pasted images are not attachments of the item, so
    get_jama_item_attachments doesn't list them; use `embedded_files` instead.

    Args:
        item_id: The ID (as a string) of the Jama item to retrieve.
        include_images: If true, also fetch the item's embedded images and return
            them inline after the item, saving a get_jama_attachment_content call.
            Leave false unless you need to see the images: they use a lot of
            context. At most 10 images of up to 5 MB each are inlined; any others
            are noted and can be fetched with get_jama_attachment_content.

    Returns:
        A dictionary representing the item, followed by its images if
        include_images is true.

    Raises:
        APIException: If the item is not found or an error occurs.
    """
    logger.info(f"Executing get_jama_item tool for item_id: {item_id}, include_images: {include_images}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
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
    Retrieves a list of items for a specific project from Jama Connect.

    Args:
        project_id: The ID (as a string) of the Jama project.

    Returns:
        A list of dictionaries representing items in the project.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_project_items tool for project_id: {project_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    items = jama_client.get_items(project_id=project_id)
    return items if items else []

@mcp.tool()
async def get_jama_item_children(item_id: str, ctx: Context) -> list[dict]:
    """
    Retrieves child items for a specific Jama item.

    Args:
        item_id: The ID (as a string) of the parent Jama item.

    Returns:
        A list of dictionaries representing child items.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_item_children tool for parent_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    children = jama_client.get_item_children(item_id=item_id)
    return children if children else []

@mcp.tool()
async def get_jama_relationships(project_id: str, ctx: Context) -> list[dict]:
    """
    Retrieves all relationships within a specific Jama project.

    Args:
        project_id: The ID (as a string) of the Jama project.

    Returns:
        A list of dictionaries representing relationships.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_relationships tool for project_id: {project_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    relationships = jama_client.get_relationships(project_id=project_id)
    return relationships if relationships else []

@mcp.tool()
async def get_jama_relationship(relationship_id: str, ctx: Context) -> dict:
    """
    Retrieves details for a specific relationship by its ID.

    Args:
        relationship_id: The ID (as a string) of the relationship.

    Returns:
        A dictionary representing the relationship.

    Raises:
        APIException: If the relationship is not found or an error occurs.
    """
    logger.info(f"Executing get_jama_relationship tool for relationship_id: {relationship_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    relationship = jama_client.get_relationship(relationship_id=relationship_id)
    # Let py-jama-rest-client raise ResourceNotFoundException if applicable
    if not relationship and MOCK_MODE: # Handle mock case explicitly if needed
         raise ValueError(f"Mock Relationship with ID {relationship_id} not found.")
    return relationship

@mcp.tool()
async def get_jama_item_upstream_relationships(item_id: str, ctx: Context) -> list[dict]:
    """
    Retrieves upstream relationships for a specific Jama item.

    Args:
        item_id: The ID (as a string) of the Jama item.

    Returns:
        A list of dictionaries representing upstream relationships.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_item_upstream_relationships tool for item_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    relationships = jama_client.get_items_upstream_relationships(item_id=item_id)
    return relationships if relationships else []

@mcp.tool()
async def get_jama_item_downstream_relationships(item_id: str, ctx: Context) -> list[dict]:
    """
    Retrieves downstream relationships for a specific Jama item.

    Args:
        item_id: The ID (as a string) of the Jama item.

    Returns:
        A list of dictionaries representing downstream relationships.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_item_downstream_relationships tool for item_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    relationships = jama_client.get_items_downstream_relationships(item_id=item_id)
    return relationships if relationships else []

@mcp.tool()
async def get_jama_item_upstream_related(item_id: str, ctx: Context) -> list[dict]:
    """
    Retrieves upstream related items for a specific Jama item.

    Args:
        item_id: The ID (as a string) of the Jama item.

    Returns:
        A list of dictionaries representing upstream related items.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_item_upstream_related tool for item_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    items = jama_client.get_items_upstream_related(item_id=item_id)
    return items if items else []

@mcp.tool()
async def get_jama_item_downstream_related(item_id: str, ctx: Context) -> list[dict]:
    """
    Retrieves downstream related items for a specific Jama item.

    Args:
        item_id: The ID (as a string) of the Jama item.

    Returns:
        A list of dictionaries representing downstream related items.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_item_downstream_related tool for item_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    items = jama_client.get_items_downstream_related(item_id=item_id)
    return items if items else []

@mcp.tool()
async def get_jama_item_attachments(item_id: str, ctx: Context) -> list[dict]:
    """
    Lists the attachments associated with a Jama item: their REST API IDs, file
    names, MIME types, and sizes. Pass the IDs to get_jama_attachment_content's
    attachment_ids to fetch the files.

    Images pasted into an item's rich text are not attachments and are not listed
    here. For files referenced from rich text, use the `embedded_files` URLs from
    get_jama_item instead. The number in such a URL (e.g. the `21627` in
    `https://<host>/attachment/21627/diagram.png`) is not a REST attachment ID.

    Args:
        item_id: The ID (as a string) of the Jama item.

    Returns:
        A list of dictionaries, one per attachment, each with `id`, `file_name`,
        `mime_type`, and `file_size`.

    Raises:
        APIException: If the item is not found or an error occurs.
    """
    logger.info(f"Executing get_jama_item_attachments tool for item_id: {item_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    attachments = list_item_attachments(jama_client, item_id)
    return [a._asdict() for a in attachments]

@mcp.tool()
async def get_jama_attachment_content(
    ctx: Context,
    urls: list[str] | None = None,
    attachment_ids: list[str] | None = None,
):
    """
    Retrieves the file content of one or more Jama files in a single call, e.g. the
    images in an item's rich-text description.

    Identify the files with either or both of:
    - urls: URLs of files referenced from an item's rich-text fields, as listed in
      get_jama_item's `embedded_files` (or an <img src> / <a href> pointing at
      `https://<host>/attachment/...`). This works for images pasted into rich text
      as well as attachments referenced from it. Pass all the files you need at
      once rather than calling this tool once per file.
    - attachment_ids: REST API attachment IDs, as returned by
      get_jama_item_attachments, for attachments not referenced from rich text.
      The number in an `/attachment/<n>/...` URL is not an attachment ID.

    Args:
        urls: URLs of files referenced from rich text.
        attachment_ids: REST API IDs of attachments.

    Returns:
        For each file, a text line naming it, followed by the file: an inline image
        for image files (e.g. PNG, JPEG), otherwise an embedded resource containing
        the base64-encoded content and its MIME type. A file that can't be fetched
        gets a text line with the error instead, without failing the others.

    Raises:
        ValueError: If neither urls nor attachment_ids are given.
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
    Retrieves all item types from Jama Connect.

    Returns:
        A list of dictionaries representing item types.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info("Executing get_jama_item_types tool")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    item_types = jama_client.get_item_types()
    return item_types if item_types else []

@mcp.tool()
async def get_jama_item_type(item_type_id: str, ctx: Context) -> dict:
    """
    Retrieves details for a specific item type by its ID.

    Args:
        item_type_id: The ID (as a string) of the item type.

    Returns:
        A dictionary representing the item type.

    Raises:
        APIException: If the item type is not found or an error occurs.
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
    Retrieves all pick lists from Jama Connect.

    Returns:
        A list of dictionaries representing pick lists.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info("Executing get_jama_pick_lists tool")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    pick_lists = jama_client.get_pick_lists()
    return pick_lists if pick_lists else []

@mcp.tool()
async def get_jama_pick_list(pick_list_id: str, ctx: Context) -> dict:
    """
    Retrieves details for a specific pick list by its ID.

    Args:
        pick_list_id: The ID (as a string) of the pick list.

    Returns:
        A dictionary representing the pick list.

    Raises:
        APIException: If the pick list is not found or an error occurs.
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
    Retrieves options for a specific pick list.

    Args:
        pick_list_id: The ID (as a string) of the pick list.

    Returns:
        A list of dictionaries representing pick list options.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_pick_list_options tool for pick_list_id: {pick_list_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    options = jama_client.get_pick_list_options(pick_list_id=pick_list_id)
    return options if options else []

@mcp.tool()
async def get_jama_pick_list_option(pick_list_option_id: str, ctx: Context) -> dict:
    """
    Retrieves details for a specific pick list option by its ID.

    Args:
        pick_list_option_id: The ID (as a string) of the pick list option.

    Returns:
        A dictionary representing the pick list option.

    Raises:
        APIException: If the pick list option is not found or an error occurs.
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
    Retrieves all tags for a specific project.

    Args:
        project_id: The ID (as a string) of the Jama project.

    Returns:
        A list of dictionaries representing tags.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_tags tool for project_id: {project_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    tags = jama_client.get_tags(project=project_id) # Param name is 'project' in client
    return tags if tags else []

@mcp.tool()
async def get_jama_tagged_items(tag_id: str, ctx: Context) -> list[dict]:
    """
    Retrieves items associated with a specific tag.

    Args:
        tag_id: The ID (as a string) of the tag.

    Returns:
        A list of dictionaries representing items associated with the tag.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_tagged_items tool for tag_id: {tag_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    items = jama_client.get_tagged_items(tag_id=tag_id)
    return items if items else []

@mcp.tool()
async def get_jama_test_cycle(test_cycle_id: str, ctx: Context) -> dict:
    """
    Retrieves details for a specific test cycle by its ID.

    Args:
        test_cycle_id: The ID (as a string) of the test cycle.

    Returns:
        A dictionary representing the test cycle.

    Raises:
        APIException: If the test cycle is not found or an error occurs.
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
    Retrieves test runs associated with a specific test cycle.

    Args:
        test_cycle_id: The ID (as a string) of the test cycle.

    Returns:
        A list of dictionaries representing test runs.

    Raises:
        APIException: If an error occurs during the Jama API call.
    """
    logger.info(f"Executing get_jama_test_runs tool for test_cycle_id: {test_cycle_id}")
    jama_client: JamaClient = ctx.request_context.lifespan_context["jama_client"]
    runs = jama_client.get_testruns(test_cycle_id=test_cycle_id)
    return runs if runs else []



@mcp.tool()
async def test_jama_connection(ctx: Context) -> dict:
    """
    Tests the connection and authentication to the Jama Connect API.
    Attempts to fetch available API endpoints as a lightweight check.

    Returns:
        A dictionary containing the result of the get_available_endpoints call.

    Raises:
        APIException: If the connection test fails.
        ValueError: If the JamaClient is not found in the context.
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

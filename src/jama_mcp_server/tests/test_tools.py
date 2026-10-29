import pytest
from unittest.mock import MagicMock

# Import the tool functions we want to test
from jama_mcp_server.attachments import AttachmentFile
from jama_mcp_server.server import (
    get_jama_projects,
    get_jama_item,
    get_jama_project_items,
    get_jama_attachment_content,
    get_jama_item_attachments,
)

# --- Test Fixtures ---

@pytest.fixture
def mock_jama_client():
    """Provides a basic mock JamaClient instance."""
    return MagicMock()

@pytest.fixture
def mock_context(mock_jama_client):
    """Provides a simplified mock MCP Context containing the mock JamaClient."""
    mock_ctx = MagicMock()
    # Simulate the structure the tool function expects to find the client
    mock_ctx.request_context.lifespan_context = {"jama_client": mock_jama_client}
    return mock_ctx

# --- Tool Tests for get_jama_projects ---

@pytest.mark.asyncio
async def test_get_jama_projects_success(mock_context, mock_jama_client):
    """Test get_jama_projects returns data from mock client."""
    # Arrange: Configure the mock client's method
    mock_projects_data = [{"id": 1, "name": "Proj A"}, {"id": 2, "name": "Proj B"}]
    mock_jama_client.get_projects.return_value = mock_projects_data

    # Act: Call the tool function
    result = await get_jama_projects(mock_context)

    # Assert: Check the return value and that the mock was called
    assert result == mock_projects_data
    mock_jama_client.get_projects.assert_called_once()

@pytest.mark.asyncio
async def test_get_jama_projects_empty(mock_context, mock_jama_client):
    """Test get_jama_projects returns empty list when client does."""
    # Arrange
    mock_jama_client.get_projects.return_value = []

    # Act
    result = await get_jama_projects(mock_context)

    # Assert
    assert result == []
    mock_jama_client.get_projects.assert_called_once()

@pytest.mark.asyncio
async def test_get_jama_projects_error(mock_context, mock_jama_client):
    """Test get_jama_projects propagates exceptions from the client."""
    # Arrange: Configure the mock client to raise an error
    mock_jama_client.get_projects.side_effect = ConnectionError("API unavailable")

    # Act & Assert: Check that the exception is raised
    with pytest.raises(ConnectionError, match="API unavailable"):
        await get_jama_projects(mock_context)

    # Assert: Check the mock was still called
    mock_jama_client.get_projects.assert_called_once()
# --- Tool Tests for get_jama_item ---

@pytest.mark.asyncio
async def test_get_jama_item_success(mock_context, mock_jama_client):
    """Test get_jama_item returns data for a specific ID."""
    # Arrange
    item_id_to_test = "123"
    mock_item_data = {"id": 123, "name": "Item 123"}
    mock_jama_client.get_item.return_value = mock_item_data

    # Act
    result = await get_jama_item(item_id=item_id_to_test, ctx=mock_context)

    # Assert
    assert result == {**mock_item_data, "embedded_files": []}
    mock_jama_client.get_item.assert_called_once_with(item_id_to_test)

@pytest.mark.asyncio
async def test_get_jama_item_not_found(mock_context, mock_jama_client):
    """Test get_jama_item returns None when client returns None."""
    # Arrange
    item_id_to_test = "999"
    mock_jama_client.get_item.return_value = None # Simulate client returning None

    # Act
    result = await get_jama_item(item_id=item_id_to_test, ctx=mock_context)

    # Assert
    assert result is None
    mock_jama_client.get_item.assert_called_once_with(item_id_to_test)

@pytest.mark.asyncio
async def test_get_jama_item_error(mock_context, mock_jama_client):
    """Test get_jama_item propagates exceptions from the client."""
    # Arrange
    item_id_to_test = "123"
    mock_jama_client.get_item.side_effect = TimeoutError("Request timed out")

    # Act & Assert
    with pytest.raises(TimeoutError, match="Request timed out"):
        await get_jama_item(item_id=item_id_to_test, ctx=mock_context)

    # Assert
    mock_jama_client.get_item.assert_called_once_with(item_id_to_test)


@pytest.mark.asyncio
async def test_get_jama_item_by_document_key(mock_context, mock_jama_client):
    """Test get_jama_item looks up a documentKey and fetches the matching item."""
    mock_jama_client.get_abstract_items.return_value = [{"id": 123, "documentKey": "ABC-REQ-12", "type": "items"}]
    mock_jama_client.get_item.return_value = {"id": 123, "fields": {}}

    result = await get_jama_item(item_id="abc-req-12", ctx=mock_context)

    assert result["id"] == 123
    mock_jama_client.get_abstract_items.assert_called_once_with(document_key=["abc-req-12"])
    mock_jama_client.get_item.assert_called_once_with("123")

@pytest.mark.asyncio
async def test_get_jama_item_unknown_document_key(mock_context, mock_jama_client):
    """Test get_jama_item raises when only a non-item (e.g. an attachment) has the documentKey."""
    mock_jama_client.get_abstract_items.return_value = [{"id": 5, "documentKey": "ABC-REQ-99", "type": "attachments"}]

    with pytest.raises(ValueError, match="No Jama item with ID or documentKey 'ABC-REQ-99'"):
        await get_jama_item(item_id="ABC-REQ-99", ctx=mock_context)
    mock_jama_client.get_item.assert_not_called()

@pytest.mark.asyncio
async def test_get_jama_item_attachments_by_document_key(mock_context, mock_jama_client):
    """Test item tools other than get_jama_item also accept a documentKey."""
    mock_jama_client.get_abstract_items.return_value = [{"id": 140309, "documentKey": "ABC-REQ-12", "type": "items"}]
    mock_jama_client.list_item_attachments.return_value = []

    await get_jama_item_attachments(item_id="ABC-REQ-12", ctx=mock_context)

    mock_jama_client.list_item_attachments.assert_called_once_with("140309")


# --- Tool Tests for get_jama_project_items ---

@pytest.mark.asyncio
async def test_get_jama_project_items_success(mock_context, mock_jama_client):
    """Test get_jama_project_items returns items for a project."""
    # Arrange
    project_id_to_test = "1"
    mock_items_data = [{"id": 10, "name": "Item A"}, {"id": 11, "name": "Item B"}]
    mock_jama_client.get_items.return_value = mock_items_data

    # Act
    result = await get_jama_project_items(project_id=project_id_to_test, ctx=mock_context)

    # Assert
    assert result == mock_items_data
    mock_jama_client.get_items.assert_called_once_with(project_id=project_id_to_test)

@pytest.mark.asyncio
async def test_get_jama_project_items_empty(mock_context, mock_jama_client):
    """Test get_jama_project_items returns empty list when client does."""
    # Arrange
    project_id_to_test = "2"
    mock_jama_client.get_items.return_value = [] # Simulate empty list from client

    # Act
    result = await get_jama_project_items(project_id=project_id_to_test, ctx=mock_context)

    # Assert
    assert result == []
    mock_jama_client.get_items.assert_called_once_with(project_id=project_id_to_test)

@pytest.mark.asyncio
async def test_get_jama_project_items_error(mock_context, mock_jama_client):
    """Test get_jama_project_items propagates exceptions from the client."""
    # Arrange
    project_id_to_test = "1"
    mock_jama_client.get_items.side_effect = ValueError("Invalid Project ID format")

    # Act & Assert
    with pytest.raises(ValueError, match="Invalid Project ID format"):
        await get_jama_project_items(project_id=project_id_to_test, ctx=mock_context)

    # Assert
    mock_jama_client.get_items.assert_called_once_with(project_id=project_id_to_test)


# --- Tool Tests for get_jama_item_attachments ---

@pytest.mark.asyncio
async def test_get_jama_item_attachments_success(mock_context, mock_jama_client):
    """Test get_jama_item_attachments resolves the REST attachment id for an item."""
    from jama_mcp_server.attachments import AttachmentInfo

    item_id_to_test = "140309"
    mock_jama_client.list_item_attachments.return_value = [
        AttachmentInfo(id=315628, file_name="diagram.png", mime_type="image/png", file_size=151783)
    ]

    result = await get_jama_item_attachments(item_id=item_id_to_test, ctx=mock_context)

    assert result == [{"id": 315628, "file_name": "diagram.png", "mime_type": "image/png", "file_size": 151783}]
    mock_jama_client.list_item_attachments.assert_called_once_with(item_id_to_test)


# --- Tool Tests for get_jama_item embedded files ---

PASTED_IMAGE_URL = "https://example.jamacloud.com/attachment/22129/image-anEE2T6Ebh.png"
LINKED_PDF_URL = "https://example.jamacloud.com/attachment/22130/spec%20v2.pdf"
ITEM_WITH_EMBEDDED_FILES = {
    "id": 319839,
    "fields": {
        "name": "Voltage monitoring",
        "description": (
            '<p><a href="https://example.jamacloud.com/perspective.req?docId=1&amp;projectId=50">other item</a></p>'
            f'<p><img alt="block diagram" height="301" src="{PASTED_IMAGE_URL}" width="373" /></p>'
            f'<p><a href="{LINKED_PDF_URL}">spec</a></p>'
        ),
        "rationale$123": f'<img src="{PASTED_IMAGE_URL}" />',
    },
}


@pytest.mark.asyncio
async def test_get_jama_item_lists_embedded_files(mock_context, mock_jama_client):
    """Test get_jama_item lists rich-text file references, skipping non-attachment links and duplicates."""
    mock_jama_client.get_item.return_value = ITEM_WITH_EMBEDDED_FILES

    result = await get_jama_item(item_id="319839", ctx=mock_context)

    assert result["embedded_files"] == [
        {"field": "description", "url": PASTED_IMAGE_URL, "file_name": "image-anEE2T6Ebh.png", "kind": "image", "alt": "block diagram"},
        {"field": "description", "url": LINKED_PDF_URL, "file_name": "spec v2.pdf", "kind": "link", "alt": None},
    ]
    mock_jama_client.get_embedded_file.assert_not_called()


@pytest.mark.asyncio
async def test_get_jama_item_resolves_relative_urls(mock_context, mock_jama_client, monkeypatch):
    """Test get_jama_item resolves relative rich-text URLs against JAMA_URL."""
    monkeypatch.setenv("JAMA_URL", "https://example.jamacloud.com")
    mock_jama_client.get_item.return_value = {"id": 1, "fields": {"description": '<img src="/attachment/5/a.png" />'}}

    result = await get_jama_item(item_id="1", ctx=mock_context)

    assert result["embedded_files"][0]["url"] == "https://example.jamacloud.com/attachment/5/a.png"


@pytest.mark.asyncio
async def test_get_jama_item_include_images_inlines_images_only(mock_context, mock_jama_client):
    """Test get_jama_item(include_images=True) returns the item plus its images, but not linked files."""
    from mcp.types import CallToolResult, ImageContent

    mock_jama_client.get_item.return_value = ITEM_WITH_EMBEDDED_FILES
    mock_jama_client.get_embedded_file.return_value = AttachmentFile(
        content=b"\x89PNG\r\n\x1a\nfakepngbytes", content_type="image/png", filename="image-anEE2T6Ebh.png"
    )

    result = await get_jama_item(item_id="319839", ctx=mock_context, include_images=True)

    assert isinstance(result, CallToolResult)
    assert result.structuredContent["embedded_files"][0]["url"] == PASTED_IMAGE_URL
    assert [type(c).__name__ for c in result.content] == ["TextContent", "TextContent", "ImageContent"]
    assert isinstance(result.content[2], ImageContent)
    mock_jama_client.get_embedded_file.assert_called_once_with(PASTED_IMAGE_URL)


@pytest.mark.asyncio
async def test_get_jama_item_include_images_caps_count(mock_context, mock_jama_client, monkeypatch):
    """Test get_jama_item(include_images=True) notes images beyond the limit instead of fetching them."""
    from jama_mcp_server import server

    monkeypatch.setattr(server, "MAX_INLINE_IMAGES", 1)
    mock_jama_client.get_item.return_value = {
        "id": 1,
        "fields": {"description": '<img src="https://h/attachment/1/a.png" /><img src="https://h/attachment/2/b.png" />'},
    }
    mock_jama_client.get_embedded_file.return_value = AttachmentFile(
        content=b"\x89PNG\r\n\x1a\n", content_type="image/png", filename="a.png"
    )

    result = await get_jama_item(item_id="1", ctx=mock_context, include_images=True)

    assert "over the 1-image limit" in result.content[-1].text
    mock_jama_client.get_embedded_file.assert_called_once_with("https://h/attachment/1/a.png")


# --- Tool Tests for get_jama_attachment_content ---

@pytest.mark.asyncio
async def test_get_jama_attachment_content_fetches_urls_and_ids_in_one_call(mock_context, mock_jama_client):
    """Test get_jama_attachment_content returns every requested file, each preceded by a label."""
    from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent, TextContent

    mock_jama_client.get_embedded_file.return_value = AttachmentFile(
        content=b"\x89PNG\r\n\x1a\nfakepngbytes", content_type="image/png", filename="image-anEE2T6Ebh.png"
    )
    mock_jama_client.get_attachment_file.return_value = AttachmentFile(
        content=b"%PDF-1.4 fake pdf bytes", content_type="application/pdf", filename="spec.pdf"
    )

    result = await get_jama_attachment_content(urls=[PASTED_IMAGE_URL], attachment_ids=["702"], ctx=mock_context)

    assert [type(c) for c in result] == [TextContent, ImageContent, TextContent, EmbeddedResource]
    assert result[1].mimeType == "image/png"
    assert isinstance(result[3].resource, BlobResourceContents)
    assert result[3].resource.mimeType == "application/pdf"
    assert "spec.pdf" in result[2].text
    mock_jama_client.get_embedded_file.assert_called_once_with(PASTED_IMAGE_URL)
    mock_jama_client.get_attachment_file.assert_called_once_with("702")


@pytest.mark.asyncio
async def test_get_jama_attachment_content_reports_failures_without_failing_others(mock_context, mock_jama_client):
    """Test a file that can't be fetched becomes an error line, and the rest are still returned."""
    from mcp.types import ImageContent

    mock_jama_client.get_embedded_file.side_effect = [
        ValueError("not found"),
        AttachmentFile(content=b"\x89PNG\r\n\x1a\n", content_type="image/png", filename="b.png"),
    ]

    result = await get_jama_attachment_content(
        urls=["https://h/attachment/1/a.png", "https://h/attachment/2/b.png"], ctx=mock_context
    )

    assert "Failed to fetch https://h/attachment/1/a.png: not found" in result[0].text
    assert isinstance(result[2], ImageContent)


@pytest.mark.asyncio
async def test_get_jama_attachment_content_requires_urls_or_ids(mock_context, mock_jama_client):
    """Test get_jama_attachment_content rejects a call with nothing to fetch."""
    with pytest.raises(ValueError, match="Provide urls and/or attachment_ids"):
        await get_jama_attachment_content(ctx=mock_context)

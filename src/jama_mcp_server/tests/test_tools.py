import pytest
from unittest.mock import MagicMock

# Import the tool functions we want to test
from jama_mcp_server.attachments import AttachmentFile
from jama_mcp_server.server import (
    get_jama_projects,
    get_jama_item,
    get_jama_project_items,
    get_jama_attachment,
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
    assert result == mock_item_data
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


# --- Tool Tests for get_jama_attachment ---

@pytest.mark.asyncio
async def test_get_jama_attachment_success(mock_context, mock_jama_client):
    """Test get_jama_attachment returns metadata from mock client."""
    attachment_id_to_test = "701"
    mock_attachment_data = {"id": 701, "fields": {"name": "diagram.png"}}
    mock_jama_client.get_attachment.return_value = mock_attachment_data

    result = await get_jama_attachment(attachment_id=attachment_id_to_test, ctx=mock_context)

    assert result == mock_attachment_data
    mock_jama_client.get_attachment.assert_called_once_with(attachment_id_to_test)


# --- Tool Tests for get_jama_attachment_content ---

@pytest.mark.asyncio
async def test_get_jama_attachment_content_image_returns_image_content(mock_context, mock_jama_client):
    """Test get_jama_attachment_content returns MCP ImageContent for an image attachment."""
    from mcp.types import ImageContent

    attachment_id_to_test = "701"
    mock_jama_client.get_attachment_file.return_value = AttachmentFile(
        content=b"\x89PNG\r\n\x1a\nfakepngbytes",
        content_type="image/png",
        filename="diagram.png",
    )

    result = await get_jama_attachment_content(attachment_id=attachment_id_to_test, ctx=mock_context)

    image_content = result.to_image_content()
    assert isinstance(image_content, ImageContent)
    assert image_content.mimeType == "image/png"
    mock_jama_client.get_attachment_file.assert_called_once_with(attachment_id_to_test)


@pytest.mark.asyncio
async def test_get_jama_attachment_content_non_image_returns_embedded_resource(mock_context, mock_jama_client):
    """Test get_jama_attachment_content returns an EmbeddedResource for non-image attachments."""
    from mcp.types import BlobResourceContents, EmbeddedResource

    attachment_id_to_test = "702"
    mock_jama_client.get_attachment_file.return_value = AttachmentFile(
        content=b"%PDF-1.4 fake pdf bytes",
        content_type="application/pdf",
        filename="spec.pdf",
    )

    result = await get_jama_attachment_content(attachment_id=attachment_id_to_test, ctx=mock_context)

    assert isinstance(result, EmbeddedResource)
    assert isinstance(result.resource, BlobResourceContents)
    assert result.resource.mimeType == "application/pdf"
    mock_jama_client.get_attachment_file.assert_called_once_with(attachment_id_to_test)


@pytest.mark.asyncio
async def test_get_jama_attachment_content_not_found_propagates(mock_context, mock_jama_client):
    """Test get_jama_attachment_content propagates a not-found error from the client."""
    attachment_id_to_test = "999"
    mock_jama_client.get_attachment_file.side_effect = ValueError("Attachment not found")

    with pytest.raises(ValueError, match="Attachment not found"):
        await get_jama_attachment_content(attachment_id=attachment_id_to_test, ctx=mock_context)


@pytest.mark.asyncio
async def test_get_jama_attachment_content_resolves_via_item_id_and_file_name(mock_context, mock_jama_client):
    """Test get_jama_attachment_content resolves the id itself when given item_id + file_name."""
    from jama_mcp_server.attachments import AttachmentInfo

    mock_jama_client.list_item_attachments.return_value = [
        AttachmentInfo(id=315628, file_name="diagram.png", mime_type="image/png", file_size=151783)
    ]
    mock_jama_client.get_attachment_file.return_value = AttachmentFile(
        content=b"\x89PNG\r\n\x1a\nfakepngbytes",
        content_type="image/png",
        filename="diagram.png",
    )

    result = await get_jama_attachment_content(item_id="140309", file_name="diagram.png", ctx=mock_context)

    result.to_image_content()  # doesn't raise
    mock_jama_client.list_item_attachments.assert_called_once_with("140309")
    mock_jama_client.get_attachment_file.assert_called_once_with("315628")


@pytest.mark.asyncio
async def test_get_jama_attachment_content_requires_id_or_item_and_filename(mock_context, mock_jama_client):
    """Test get_jama_attachment_content rejects a call with none of its identifying args."""
    with pytest.raises(ValueError, match="Provide either attachment_id"):
        await get_jama_attachment_content(ctx=mock_context)


@pytest.mark.asyncio
async def test_get_jama_attachment_content_file_name_not_found(mock_context, mock_jama_client):
    """Test get_jama_attachment_content raises when no attachment matches file_name."""
    mock_jama_client.list_item_attachments.return_value = []

    with pytest.raises(Exception, match="No attachment named"):
        await get_jama_attachment_content(item_id="140309", file_name="missing.png", ctx=mock_context)


@pytest.mark.asyncio
async def test_get_jama_attachment_content_file_name_ambiguous(mock_context, mock_jama_client):
    """Test get_jama_attachment_content raises when file_name matches more than one attachment."""
    from jama_mcp_server.attachments import AttachmentInfo

    mock_jama_client.list_item_attachments.return_value = [
        AttachmentInfo(id=175819, file_name="Protective state.txt", mime_type="text/plain", file_size=1321),
        AttachmentInfo(id=211114, file_name="Protective state.txt", mime_type="text/plain", file_size=1321),
    ]

    with pytest.raises(Exception, match="2 attachments named"):
        await get_jama_attachment_content(item_id="103", file_name="Protective state.txt", ctx=mock_context)
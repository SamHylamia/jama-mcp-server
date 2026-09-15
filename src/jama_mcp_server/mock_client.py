import logging

from .attachments import AttachmentFile, AttachmentInfo

logger = logging.getLogger(__name__)

# A minimal valid 1x1 red PNG, used as mock attachment file content.
_MOCK_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae"
    "426082"
)

class MockJamaClient:
    """
    A mock implementation of the JamaClient for testing the MCP server
    without connecting to a real Jama instance.
    """
    def get_projects(self):
        logger.info("MOCK: get_projects() called")
        return [{"id": 1, "name": "Mock Project Alpha", "projectKey": "MPA"},
                {"id": 2, "name": "Mock Project Beta", "projectKey": "MPB"}]

    def get_item(self, item_id: str): # Changed type hint to str
        logger.info(f"MOCK: get_item(item_id='{item_id}') called")
        # Compare as strings or convert if necessary for logic
        if item_id == "123":
            return {"id": 123, "documentKey": "MOCK-1", "fields": {"name": "Mock Item 123", "description": "A sample item."}}
        elif item_id == "456":
             return {"id": 456, "documentKey": "MOCK-2", "fields": {"name": "Another Mock Item", "description": "Details here."}}
        else:
            # Simulate not found for other IDs in mock mode
            logger.warning(f"MOCK: Item ID {item_id} not found.")
            return None # Or raise an appropriate exception if the real client does

    def get_available_endpoints(self):
         logger.info("MOCK: get_available_endpoints() called")
         # Return a structure similar to the real API if known, otherwise simple dict
         return {"data": [{"path": "/mock", "method": "GET"}]}

    # Note: The real client's get_items takes project_id as a keyword argument
    # Note: The real client's get_items takes project_id as a keyword argument
    def get_items(self, project_id: str = None): # Changed type hint to str, kept default None
        if project_id is not None:
            logger.info(f"MOCK: get_items(project_id='{project_id}') called")
            # Compare as strings or convert if necessary for logic
            if project_id == "1": # Mock Project Alpha
                # Use self.get_item with string ID
                item = self.get_item("123")
                return [item] if item else []
            elif project_id == "2": # Mock Project Beta
                 item = self.get_item("456")
                 return [item] if item else []
            else:
                logger.warning(f"MOCK: Project ID {project_id} not found for get_items.")
                return [] # No items for other mock projects
        else:
             # Mock behavior if get_items is called without project_id (if applicable)
             logger.warning("MOCK: get_items() called without project_id, returning empty list.")
             return []

    def get_item_children(self, item_id: str): # Use str for ID
        logger.info(f"MOCK: get_item_children(item_id='{item_id}') called")
        if item_id == "123": # Compare as string
            # Return mock children for item 123
            return [{"id": 789, "documentKey": "MOCK-3", "fields": {"name": "Child Item 1", "description": "Child of 123"}},
                    {"id": 790, "documentKey": "MOCK-4", "fields": {"name": "Child Item 2", "description": "Another child of 123"}}]
        else:
            # Return empty list for other items in mock mode
            logger.warning(f"MOCK: Parent item ID '{item_id}' not found or has no children.")
            return []

    def get_relationships(self, project_id: str):
        logger.info(f"MOCK: get_relationships(project_id='{project_id}') called")
        if project_id == "1":
            return [{"id": 101, "fromItem": 123, "toItem": 789, "relationshipType": 1},
                    {"id": 102, "fromItem": 790, "toItem": 123, "relationshipType": 2}]
        return []

    def get_relationship(self, relationship_id: str):
        logger.info(f"MOCK: get_relationship(relationship_id='{relationship_id}') called")
        if relationship_id == "101":
            return {"id": 101, "fromItem": 123, "toItem": 789, "relationshipType": 1}
        return None

    def get_items_upstream_relationships(self, item_id: str):
        logger.info(f"MOCK: get_items_upstream_relationships(item_id='{item_id}') called")
        if item_id == "789":
             return [{"id": 101, "fromItem": 123, "toItem": 789, "relationshipType": 1}]
        return []

    def get_items_downstream_relationships(self, item_id: str):
         logger.info(f"MOCK: get_items_downstream_relationships(item_id='{item_id}') called")
         if item_id == "123":
             return [{"id": 101, "fromItem": 123, "toItem": 789, "relationshipType": 1}]
         return []

    def get_items_upstream_related(self, item_id: str):
        logger.info(f"MOCK: get_items_upstream_related(item_id='{item_id}') called")
        if item_id == "789":
            return [self.get_item("123")]
        return []

    def get_items_downstream_related(self, item_id: str):
        logger.info(f"MOCK: get_items_downstream_related(item_id='{item_id}') called")
        if item_id == "123":
            return [self.get_item("789")]
        return []

    def get_item_types(self):
        logger.info("MOCK: get_item_types() called")
        return [{"id": 10, "name": "Requirement", "typeKey": "REQ"},
                {"id": 11, "name": "Test Case", "typeKey": "TC"}]

    def get_item_type(self, item_type_id: str):
        logger.info(f"MOCK: get_item_type(item_type_id='{item_type_id}') called")
        if item_type_id == "10":
            return {"id": 10, "name": "Requirement", "typeKey": "REQ"}
        return None

    def get_pick_lists(self):
        logger.info("MOCK: get_pick_lists() called")
        return [{"id": 20, "name": "Priority"}, {"id": 21, "name": "Status"}]

    def get_pick_list(self, pick_list_id: str):
        logger.info(f"MOCK: get_pick_list(pick_list_id='{pick_list_id}') called")
        if pick_list_id == "20":
            return {"id": 20, "name": "Priority"}
        return None

    def get_pick_list_options(self, pick_list_id: str):
        logger.info(f"MOCK: get_pick_list_options(pick_list_id='{pick_list_id}') called")
        if pick_list_id == "20":
            return [{"id": 201, "name": "High"}, {"id": 202, "name": "Medium"}, {"id": 203, "name": "Low"}]
        return []

    def get_pick_list_option(self, pick_list_option_id: str):
         logger.info(f"MOCK: get_pick_list_option(pick_list_option_id='{pick_list_option_id}') called")
         if pick_list_option_id == "201":
             return {"id": 201, "name": "High"}
         return None

    # Match keyword argument 'project' used by real client and server tool
    def get_tags(self, project: str):
        logger.info(f"MOCK: get_tags(project='{project}') called")
        if project == "1": # Compare using the correct parameter name
            return [{"id": 301, "name": "UI"}, {"id": 302, "name": "Backend"}]
        return []

    def get_tagged_items(self, tag_id: str):
        logger.info(f"MOCK: get_tagged_items(tag_id='{tag_id}') called")
        if tag_id == "301": # UI tag
            return [self.get_item("123")]
        return []

    def get_test_cycle(self, test_cycle_id: str):
        logger.info(f"MOCK: get_test_cycle(test_cycle_id='{test_cycle_id}') called")
        if test_cycle_id == "501":
            return {"id": 501, "name": "Cycle 1", "startDate": "2025-01-01", "endDate": "2025-01-31"}
        return None

    def get_testruns(self, test_cycle_id: str):
        logger.info(f"MOCK: get_testruns(test_cycle_id='{test_cycle_id}') called")
        if test_cycle_id == "501":
            return [{"id": 601, "name": "Run 1", "status": "PASSED"},
                    {"id": 602, "name": "Run 2", "status": "FAILED"}]
        return []

    def get_attachment(self, attachment_id: str):
        logger.info(f"MOCK: get_attachment(attachment_id='{attachment_id}') called")
        if attachment_id == "701":
            return {"id": 701, "fields": {"name": "BCU_level_block.drawio.png", "description": "Mock diagram attachment"}}
        return None

    def get_attachment_file(self, attachment_id: str) -> AttachmentFile:
        logger.info(f"MOCK: get_attachment_file(attachment_id='{attachment_id}') called")
        if attachment_id == "701":
            return AttachmentFile(content=_MOCK_PNG_BYTES, content_type="image/png", filename="BCU_level_block.drawio.png")
        raise ValueError(f"Mock Attachment file for ID {attachment_id} not found.")

    def list_item_attachments(self, item_id: str) -> list[AttachmentInfo]:
        logger.info(f"MOCK: list_item_attachments(item_id='{item_id}') called")
        if item_id == "123":
            return [AttachmentInfo(id=701, file_name="BCU_level_block.drawio.png", mime_type="image/png", file_size=len(_MOCK_PNG_BYTES))]
        return []

    # Add mock methods for other functions as needed
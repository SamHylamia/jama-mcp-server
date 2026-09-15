import asyncio
import os
import logging

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

# Configure basic logging for the client
logging.basicConfig(level=logging.INFO, format='%(asctime)s - CLIENT - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    """
    Connects to the Jama MCP server via stdio and tests its tools.
    """
    # Ensure the server runs in the correct uv environment and in mock mode
    # We use 'uv run python server.py' to ensure the server runs within its venv
    # Directly execute the python interpreter from the venv using -m
    python_executable = ".venv/bin/python3" # Relative to cwd
    server_args = ["-m", "jama_mcp_server.server"]
    server_env = {"JAMA_MOCK_MODE": "true"} # Ensure server starts in mock mode

    logger.info(f"Starting server with command: {python_executable} {' '.join(server_args)}")
    logger.info(f"Server environment: {server_env}")

    server_params = StdioServerParameters(
        command=python_executable, # The python executable from .venv
        args=server_args,          # The arguments ('-m', 'jama_mcp_server.server')
        env=server_env,
        cwd=os.path.dirname(__file__) # Run server from the script's directory (jama-mcp-server/)
    )

    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            logger.info("stdio_client connected, establishing ClientSession...")
            async with ClientSession(read_stream, write_stream) as session:
                logger.info("Initializing MCP session...")
                try:
                    init_result = await session.initialize()
                    logger.info(f"MCP Session Initialized. Server capabilities: {init_result.capabilities}")
                except Exception as init_err:
                    logger.error(f"MCP Initialization failed: {init_err}", exc_info=True)
                    return # Cannot proceed without initialization

                # --- Test Tool Calls ---
                test_results = {}

                # 1. Test Connection Tool
                logger.info("Calling tool: test_jama_connection")
                # Helper function to run tool and store result/error
                async def run_test(test_name, tool_name, arguments=None):
                    logger.info(f"Calling tool: {tool_name} with args: {arguments}")
                    try:
                        result = await session.call_tool(tool_name, arguments=arguments)
                        logger.info(f"Result ({test_name}): {result}")
                        test_results[test_name] = result # Store the full result object
                    except Exception as e:
                        # This catch might be redundant if session.call_tool handles all MCP errors
                        # but good for catching unexpected client-side issues.
                        logger.error(f"Exception during call for {test_name}: {e}", exc_info=True)
                        # Create a mock error result if exception occurs before getting MCP response
                        test_results[test_name] = types.CallToolResult(content=[types.TextContent(text=f"Client-side exception: {str(e)}")], isError=True)

                # 1. Test Connection Tool
                await run_test("test_connection", "test_jama_connection")

                # 2. Get Projects Tool
                await run_test("get_projects", "get_jama_projects")

                # 3. Get Item Tool (Existing Mock ID)
                await run_test("get_item_123", "get_jama_item", arguments={"item_id": "123"})

                # 4. Get Item Tool (Non-Existing Mock ID - Expecting error/empty from server)
                await run_test("get_item_999", "get_jama_item", arguments={"item_id": "999"})

                # 5. Get Project Items Tool (Existing Mock Project ID 1)
                await run_test("get_project_items_1", "get_jama_project_items", arguments={"project_id": "1"})

                # 6. Get Project Items Tool (Non-Existing Mock Project ID 99)
                await run_test("get_project_items_99", "get_jama_project_items", arguments={"project_id": "99"})

                # 7. Get Item Children (Parent ID 123)
                await run_test("get_item_children_123", "get_jama_item_children", arguments={"item_id": "123"})

                # 8. Get Item Children (Parent ID 999)
                await run_test("get_item_children_999", "get_jama_item_children", arguments={"item_id": "999"})

                # 9. Get Relationships (Project ID 1)
                await run_test("get_relationships_1", "get_jama_relationships", arguments={"project_id": "1"})

                # 10. Get Relationship (ID 101)
                await run_test("get_relationship_101", "get_jama_relationship", arguments={"relationship_id": "101"})

                # Add more tests for other tools as needed...
                # e.g., get_jama_item_upstream_relationships, get_jama_item_types, etc.
                # Remember to use appropriate mock IDs defined in mock_client.py

                # Example: Get Item Types
                await run_test("get_item_types", "get_jama_item_types")

                # Example: Get Tags for Project 1
                await run_test("get_tags_1", "get_jama_tags", arguments={"project_id": "1"})

                # Example: Get Test Runs for Cycle 501
                await run_test("get_test_runs_501", "get_jama_test_runs", arguments={"test_cycle_id": "501"})

                # Example: Get Item Attachments (resolves item 123's attachment to ID 701)
                await run_test("get_item_attachments_123", "get_jama_item_attachments", arguments={"item_id": "123"})

                # Example: Get Attachment Metadata (ID 701)
                await run_test("get_attachment_701", "get_jama_attachment", arguments={"attachment_id": "701"})

                # Example: Get Attachment Content (ID 701, mock PNG)
                await run_test("get_attachment_content_701", "get_jama_attachment_content", arguments={"attachment_id": "701"})


                # --- Print Summary ---
                print("\n--- Test Summary ---")
                # Check results based on isError attribute
                for test_name, result_obj in test_results.items():
                    status = "FAIL" if result_obj.isError else "PASS"
                    # Extract text content for printing, handle potential errors/multiple parts
                    content_str = ""
                    if result_obj.content:
                        content_str = " | ".join([c.text for c in result_obj.content if isinstance(c, types.TextContent)])
                    print(f"{test_name}: {status} - isError={result_obj.isError}, content='{content_str}'")
                print("--------------------\n")

            logger.info("ClientSession closed.")
        logger.info("stdio_client disconnected.")

    except Exception as e:
        logger.error(f"An error occurred during the test client execution: {e}", exc_info=True)

if __name__ == "__main__":
    # Ensure the script is run from the project root or adjust paths accordingly
    # For simplicity, assuming it's run from within the jama-mcp-server directory
    # and that the jama_mcp/server.py path is correct relative to cwd.
    asyncio.run(main())
# Jama Connect MCP Server (Unofficial)

This project provides a Model Context Protocol (MCP) server that exposes read-only tools for interacting with a Jama Connect instance. It acts as an MCP wrapper around the official [Jama Software `py-jama-rest-client`](https://github.com/JamaSoftware/py-jama-rest-client) library.

**Disclaimer:** This is a third-party, open-source project and is not officially affiliated with or endorsed by Jama Software.

**Note:** This server currently only supports read-only operations. Write operations may be added in future updates.

## Philosophy: Local Execution & Security

This MCP server is **intentionally not published** as a package on PyPI or other indices. This decision encourages users to:

1.  **Clone/Fork the Repository:** Obtain the code directly.
2.  **Inspect the Code:** Understand exactly what the server does before running it, especially concerning API interactions and credential handling.
3.  **Adapt as Needed:** Modify the code for specific enterprise requirements or security postures.

This approach prioritizes security awareness and user control over convenience, mitigating risks associated with installing potentially unverified third-party packages. Local execution by cloning the repository is the only supported method at this time.

## Prerequisites

### Building From Source

*   **Python:** Version 3.12 or higher.
*   **uv:** The Python package installer and virtual environment manager. ([Installation Guide](https://github.com/astral-sh/uv#installation))
*   **Git:** For cloning the repository.
*   **Docker** For running with docker

## Setup

### Docker

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/t-j-thomas/jama-mcp-server.git
    cd jama-mcp-server
    ```

2.  **Docker Build:**
    ```bash
    sudo docker build -t jama-mcp-server .
    ```
    This will build the docker image using the project's pyproject.tonl & uv.lock configrations

3. **For Build issues due to Certificates for the Jama Rest Client Repo:**
    
    If you see an error like `certificate verification failed: CAFile`, you can optionally
    clone the https://github.com/jamasoftware-ps/py-jama-rest-client.git repo into this directory, and uncomment

    `# py-jama-rest-client = { path = "./py-jama-rest-client", editable = true }`

    from `pyproject.toml` file and comment out

    `py-jama-rest-client = { git = "https://github.com/jamasoftware-ps/py-jama-rest-client.git" }`.

    Then,
    ```bash
    uv sync
    sudo docker build -t jama-mcp-server .
    ```

### Building From Source

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/t-j-thomas/jama-mcp-server.git
    cd jama-mcp-server
    ```

2.  **Install Dependencies:**
    Navigate into the server directory and use `uv` to create a virtual environment and install dependencies.
    ```bash
    uv sync
    ```
    This installs required dependencies, including `boto3` if you plan to use AWS Parameter Store for credentials.

## Configuration

The server requires environment variables to connect to your Jama Connect instance using **OAuth 2.0**. Credentials can be provided directly or fetched securely from AWS Parameter Store.

**Authentication Methods:**

1.  **Direct Environment Variables:**
    *   `JAMA_URL` (Required): The base URL of your Jama Connect instance (e.g., `https://yourcompany.jamacloud.com`).
    *   `JAMA_CLIENT_ID` (Required for this method): Your Jama API OAuth Client ID.
    *   `JAMA_CLIENT_SECRET` (Required for this method): Your Jama API OAuth Client Secret.
    *   If both `JAMA_CLIENT_ID` and `JAMA_CLIENT_SECRET` are set, they will be used directly, and the AWS Parameter Store configuration will be ignored.

2.  **AWS Parameter Store (Not Supported for Docker):**
    *   This method is used **only if** `JAMA_CLIENT_ID` and `JAMA_CLIENT_SECRET` are *not* both set directly in the environment.
    *   `JAMA_URL` (Required): The base URL of your Jama Connect instance.
    *   `JAMA_AWS_SECRET_PATH` (Required for this method): The full name/path of the secret in AWS Parameter Store containing your Jama credentials.
        *   The secret value **must** be a JSON string with the following structure: `{"client_id": "YOUR_JAMA_CLIENT_ID", "client_secret": "YOUR_JAMA_CLIENT_SECRET"}`.
    *   `JAMA_AWS_PROFILE` (Optional): The AWS named profile to use for authenticating to AWS. If not set, `boto3` will use its default credential resolution. Your current aws session credentials need to be valid (or refreshed if expired)
    *   **Note:** Using this method requires the `boto3` library to be installed (`uv sync` handles this) and appropriate AWS permissions for the server's execution environment to access the specified Parameter Store secret.

The server first checks for `JAMA_CLIENT_ID` and `JAMA_CLIENT_SECRET`. If both are present, they are used. Otherwise, it checks for `JAMA_AWS_SECRET_PATH` and attempts to fetch credentials from AWS. If neither method provides the necessary credentials (and Mock Mode is off), the server will fail to start.

**Mock Mode (Optional):**

For testing without connecting to a live Jama instance:

*   `JAMA_MOCK_MODE`: Set to `true` to use the built-in mock client. The server will return predefined sample data. Any other value (or omitting the variable) disables mock mode.

**Setting Environment Variables:**

Set these variables in the environment where the MCP client will launch the server process. This could be:
*   Your terminal session (`export JAMA_URL=...`).
*   Directly within the MCP client's server configuration (see below).

## Running the Server (Standalone)

You can run the server directly for basic checks using `uv` (ensure environment variables are set):

```bash
# Ensure you are in the jama-mcp-server directory
uv run python -m jama_mcp_server.server
```
## Testing with `test_mcp_client.py`

This repository includes a test client script (`test_mcp_client.py`) that can be used to directly invoke the server's tools via stdio, primarily for debugging or quick checks, especially in mock mode.

To run the test client:

```bash
# Ensure you are in the jama-mcp-server directory
# This script automatically sets JAMA_MOCK_MODE=true for the server it launches
uv run python test_mcp_client.py
```

The script will start the server, call various predefined tools (including success and expected failure cases in mock mode), print the results, and provide a summary. You can modify this script to test specific tools or scenarios.


## Integration with MCP Clients (Local Execution)

Configure your MCP client (like Cline, RooCode, Claude Desktop) to launch this server via its settings (e.g., `mcp_settings.json`).

**Example Docker Configuration (`mcp_settings.json`):**

```json
{
  "mcpServers": {
    "jama-mcp": {
      "command": "docker",
      "args": ["run", "--rm", "-i",
        "-e", "JAMA_URL", "-e", "JAMA_CLIENT_ID", "-e", "JAMA_CLIENT_SECRET", "-e", "JAMA_MOCK_MODE",
        "jama-mcp-server"
      ],
      "env": {
        "JAMA_URL": "https://your-jama-instance.com",
        "JAMA_CLIENT_ID": "your-client-id",
        "JAMA_CLIENT_SECRET": ""
      }
    },
  }
}
```

**Example UV Configuration (`mcp_settings.json`):**

```json
{
  "mcpServers": {
    "jama-mcp": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "-m",
        "jama_mcp_server.server"
      ],
      
      "cwd": "/path/to/your/clone/jama-mcp-server",
      "env": {
        "JAMA_URL": "https://your.jama.instance.com",
        "JAMA_CLIENT_ID": "your_client_id",
        "JAMA_CLIENT_SECRET": "your_client_secret",
        "JAMA_AWS_SECRET_PATH": "/path/to/your/jama/secret",
        "JAMA_AWS_PROFILE": "your-aws-profile-name",
        "JAMA_MOCK_MODE": "false"
      }
    }
  }
}
```

**Key Points:**
*   The `cwd` path is critical for `uv` to find the project context.
*   Ensure the required Jama environment variables are available to the server process.

## Available Tools

This server provides various read-only tools. See your MCP client interface for the full list after connecting. Examples include `get_jama_projects`, `get_jama_item`, `get_jama_relationships`, etc.

To retrieve an attachment referenced in an item's rich-text fields (e.g. an `<img>` tag pointing at a Jama attachment URL like `https://<host>/attachment/21627/diagram.png`), pass `get_jama_attachment_content` the item's ID and the file name from that URL (`get_jama_attachment` returns just the attachment's metadata, and `get_jama_item_attachments` lists all of an item's attachments if you need to disambiguate or browse). **Note:** the numeric ID embedded in that URL is *not* the REST API attachment ID (it's served by a separate, browser-session-authenticated endpoint with its own ID space), so it can't be used directly; the file name is what actually identifies the attachment. Image attachments come back as inline images; other file types come back as an embedded resource with base64-encoded content.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions and feedback are welcome! Please see the [CONTRIBUTING.md](CONTRIBUTING.md) file for guidelines.

## Troubleshooting

*   **Connection/Authentication Errors:**
    *   Verify `JAMA_URL` is correct.
    *   Check if `JAMA_CLIENT_ID` and `JAMA_CLIENT_SECRET` are set correctly. These take priority.
    *   If direct variables are not set, check AWS Parameter Store configuration:
        *   Verify `JAMA_AWS_SECRET_PATH` is correct.
        *   Ensure the server's execution environment has permissions to read the secret (check IAM roles/policies).
        *   Verify the secret value is valid JSON: `{"client_id": "...", "client_secret": "..."}`.
        *   Check if `boto3` is installed correctly (`uv sync`).
        *   If using `JAMA_AWS_PROFILE`, ensure the profile exists and is configured correctly.
    *   If neither method provides credentials, the server will fail to start (check logs).
    *   Check Jama API client permissions in Jama Connect itself.
*   **Tool Errors:** Check arguments passed to the tool. Consult server logs (stderr) for more details.
*   **Mock Mode Not Working:** Ensure `JAMA_MOCK_MODE` environment variable is set to exactly `true`.
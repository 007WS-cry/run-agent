import os
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))

MODEL = os.environ["MODEL_ID"]

SYSTEM = f"You are an educational file-management agent that helps users safely explore the workspace rooted at {os.getcwd()} by listing directories, locating files, inspecting file metadata, and reading or explaining file contents while clearly describing each operation and never accessing paths outside the workspace.Your final answer must always be written in the same language as the user’s query."

TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }
    }
]
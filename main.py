import os
import subprocess
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

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(pattern in command for pattern in dangerous):
        return "Error: Dangerous command blocked"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:50000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as error:
        return f"Error: {error}"

def agent_loop(messages: list) -> None:
    while True:
        response = client.messages.create(model=MODEL, system=SYSTEM, messages=messages, tools=TOOLS, max_tokens=8000)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"\033[33m$ {block.input['command']}\033[0m")
                output = run_bash(block.input["command"])
                print(output[:200])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})

if __name__ == "__main__":
    print("run-agent")
    print("输入问题，回车发送，输入q或exit退出。\n")
    history = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        response_content = history[-1]["content"]
        if (isinstance(response_content, list)):
            for block in response_content:
                if getattr(block, "type", None) == "text":
                    print(block.text)
        print()

from run_agent.runtime import agent_loop

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

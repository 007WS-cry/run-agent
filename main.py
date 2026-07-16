from run_agent.runtime import agent_loop

# 本文件是项目的命令行入口，负责持续读取用户输入、维护对话历史、调用 Agent 循环并输出模型的文本回复。

# 仅在直接运行本文件时启动交互流程，作为模块导入时不会自动读取终端输入。
if __name__ == "__main__":
    print("run-agent")
    print("输入问题，回车发送，输入q或exit退出。\n")
    history = []

    # 循环收集问题并保留上下文，遇到退出指令或终端中断时结束程序。
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

        # 兼容字典和 SDK 对象两种内容块形式，只把文本类型的最终回复打印到终端。
        if isinstance(response_content, list):
            for block in response_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    print(block.get("text", ""))
                elif getattr(block, "type", None) == "text":
                    print(block.text)
        print()

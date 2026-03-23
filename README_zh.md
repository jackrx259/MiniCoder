[English](README.md) | **中文**

# MiniCoder

> 🔬 想了解 Claude Code / Cursor 这类智能体编程助手是怎么工作的？MiniCoder 用纯 Python 实现了相同的核心思路 — 读代码，学模式。

MiniCoder 是一个轻量级、本地优先的智能体 CLI 编程助手。它用一个小巧、易读的 Python 代码库实现了 Claude Code 等工具共有的核心构建模块 — 智能体循环、工具调用、多步推理、人机协同审批、上下文管理和持久记忆，方便阅读和修改。

我开发它是因为我需要一个不会打扰我、只在我下达指令时才行动的编程智能体。它在终端中运行，能够读写文件、搜索代码库、执行 Shell 命令 — 但它始终会先展示计划并等待你的批准，然后再执行任何破坏性操作。没有意外。

无需 Electron。无需云同步。无需订阅。只需 Python、你的 API 密钥和一个 REPL。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

### 🎯 为什么选择 MiniCoder？

如果你想搞懂智能体编程助手的工作原理，阅读一个精简的实现是最快的方式：

- **通过阅读学习** — 简洁、结构清晰的 Python 代码。每个组件（智能体循环、工具调度、上下文压缩、记忆系统）都对应 Claude Code / Cursor / Windsurf 中实际使用的模式。
- **通过实践学习** — 更换模型、添加新工具、修改审批逻辑、调整提示词 — 然后观察行为如何变化。
- **真正可用** — 能通过多步推理、后台执行和会话持久化处理真实的开发任务。

---

## ✨ 功能特性

### 核心智能体循环
- **多步推理** — 规划、调用工具、检查结果、反复迭代直到任务完成（每轮最多 `max_loops` 次）。
- **人机协同审批** — 每批非只读的工具调用都会以编号计划的形式呈现。你可以批准全部、拒绝全部、选择特定步骤，或发送反馈来调整方向。
- **子智能体派遣** — 对于繁重的研究任务（例如"分析整个代码库"），会启动一个全新的子智能体，使用独立的上下文运行，仅返回文本摘要，保持主会话简洁。

### 文件与代码操作
- **精准编辑** — `replace_in_file` 进行针对性的最小差异修改，当精确文本未找到时提供模糊匹配提示；仅在必要时才进行完整重写。
- **大文件感知** — 读取前先检查行数；支持 `start_line`/`end_line` 以避免在你只需要 20 行时加载一个 5000 行的文件。
- **代码库搜索** — 在所有常见文本文件类型中进行 grep 搜索；通过 glob 模式查找文件。

### Shell 与后台任务
- **前台命令** — 运行 Shell 命令，带有安全黑名单（`rm -rf`、`format`、`del /f /s`……）和可配置的超时。
- **后台任务执行** — 长时间运行的命令（`npm install`、`pytest`、`docker build`）在后台线程中运行；完成后会自动通知智能体，对话不会中断。

### 记忆与持久化
- **技能** — 告诉智能体*"记住如何部署这个项目"*，它会将 Markdown 工作流保存到 `skills/`。技能会在每次会话启动时自动注入到系统提示中。
- **会话持久化** — 使用 `/save` 和 `/load` 保存和恢复完整的对话历史。
- **任务追踪（TodoWrite）** — 对于多步骤工作，智能体维护一个实时清单，实时将项目从 `pending` → `in_progress` → `done` 逐一勾选，让你随时了解进度。
- **三层上下文压缩** — 旧的工具结果每轮都会被静默压缩；当接近上下文限制时，整个对话会被 LLM 总结并保存为 JSONL 记录，然后用摘要替换。

### 兼容性
- **任何 OpenAI 兼容的 API** — OpenAI、Azure OpenAI、Google Gemini（通过兼容层）、本地 Ollama、DeepSeek 等。
- **prompt_toolkit TUI** — 简洁的、可键盘导航的终端界面，带有语法高亮；无需 Textual 或 curses 依赖。

---

## 📦 安装

```bash
git clone https://github.com/jackrx259/MiniCoder.git
cd MiniCoder

# uv 一步完成虚拟环境创建和依赖安装
uv sync
```

> 没有 `uv`？`pip install uv` 或查看 [uv 的文档](https://docs.astral.sh/uv/)。

---

## ⚙️ 配置

**1.** 复制示例配置：

```bash
cp config.example.json config.json
```

**2.** 编辑 `config.json`：

```json
{
    "api_key": "sk-...",
    "api_base": "https://api.openai.com/v1",
    "model": "gpt-4o",
    "max_loops": 20,
    "timeout": 60,
    "max_retries": 3
}
```

| 字段 | 描述 |
|------|------|
| `api_key` | 你的 API 密钥 — **切勿提交此文件**（已在 `.gitignore` 中） |
| `api_base` | 端点地址；可更改为 Azure、Gemini、Ollama 等 |
| `model` | 例如 `gpt-4o`、`gemini-1.5-pro`、`llama3` |
| `max_loops` | 每轮最大工具调用迭代次数（默认：`20`） |
| `timeout` | 请求超时时间，单位为秒（默认：`60`） |
| `max_retries` | 遇到 429 / 5xx 时的重试次数（默认：`3`） |
| `max_context_tokens` | 自动压缩的 Token 阈值（默认：`50000`） |
| `system_prompt` | *（可选）* 覆盖系统提示 |

**3.** 运行：

```bash
python main.py
```

---

## 🚀 CLI 参数

| 参数 | 描述 |
|------|------|
| `--auto` / `--yolo` | 自动批准所有工具调用（无需提示确认） |
| `--model MODEL` | 覆盖 `config.json` 中的模型 |
| `--max-loops N` | 覆盖每轮最大工具调用循环次数 |
| `--no-skills` | 禁用技能注入 |
| `--config PATH` | 使用自定义配置文件路径 |

---

## 💬 REPL 命令

| 命令 | 描述 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清除对话历史（保留系统提示） |
| `/history` | 显示消息计数和角色分布 |
| `/save [file]` | 保存会话到 JSON（默认：`session.json`） |
| `/load [file]` | 从 JSON 加载会话 |
| `/usage` | 显示累计 Token 用量 |
| `exit` / `quit` | 结束会话（会提示是否保存） |
| `Ctrl+C` | 中断当前智能体操作 |
| `Ctrl+D` | 立即退出 |

---

## 🔍 计划审批

在任何写入/执行操作之前，你会看到一个编号计划和一个按键提示：

| 输入 | 操作 |
|------|------|
| `y` / 回车 | 批准并运行所有步骤 |
| `n` | 拒绝整个计划 |
| `a` | 切换为全自动模式（本次会话剩余部分） |
| `1`、`1,3`、… | 仅运行指定的步骤编号 |
| 其他文本 | 作为反馈发送；智能体修改计划 |

纯只读的工具批次会被静默批准，不会显示为计划。

---

## 🛠 可用工具

| 工具 | 描述 |
|------|------|
| `read_file` | 读取文件（支持 `start_line`/`end_line`） |
| `write_file` | 完整文件覆写 |
| `append_to_file` | 无需先读取即可追加内容 |
| `replace_in_file` | 精准替换，带模糊匹配提示 |
| `get_file_info` | 读取大文件前获取行数和大小 |
| `list_dir` | 目录列表，含文件大小 |
| `find_files` | 通过 glob 查找文件（例如 `src/**/*.ts`） |
| `search_files` | 在所有常见文本类型中进行 grep 搜索 |
| `get_cwd` | 获取当前工作目录 |
| `change_dir` | 更改工作目录 |
| `run_command` | 前台 Shell 命令（黑名单 + 超时） |
| `run_background` | 后台命令；完成后自动通知 |
| `check_background` | 轮询正在运行的后台任务 |
| `todo_write` | 创建/更新实时任务清单 |
| `create_skill` | 保存可复用的工作流供未来会话使用 |
| `list_skills` | 列出所有已保存的技能 |
| `delete_skill` | 删除过时的技能 |
| `dispatch_task` | 为大型研究任务派遣子智能体 |

---

## 🧠 技能（持久记忆）

只需告诉智能体记住某件事一次 — 它会将 Markdown 工作流写入 `skills/`，下次自动加载。你可以使用 `list_skills` 和 `delete_skill` 管理技能，或者直接让智能体来做。`skills/` 目录已被 gitignore，你的个人工作流保留在本地。

---

## 💾 会话持久化

- 启动时，如果 `session.json` 存在，会提示你是否恢复。
- 使用 `/save` 创建检查点；使用 `/load` 稍后恢复。
- 退出时，会提示你是否在关闭前保存。

> `session.json` 可能包含敏感的对话内容 — 默认已被 gitignore。

---

## 🗜 上下文管理

长会话通过三层机制处理：

1. **微压缩（每轮）** — 最近 6 条之前的旧工具结果被替换为简短占位符（`[used read_file]`）。对用户不可见；释放 Token。
2. **自动压缩（阈值触发）** — 当估计的 Token 数超过 `max_context_tokens` 时，完整对话被保存为 JSONL 记录到 `.transcripts/`，并用 LLM 生成的摘要替代。
3. **手动** — `/clear` 重置对话，同时保留系统提示和技能。

---

## 许可证

[MIT](LICENSE)

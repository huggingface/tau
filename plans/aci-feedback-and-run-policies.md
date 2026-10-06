# P0+P1 实施方案：ACI 纠错反馈 + 运行策略层

> 目标：借鉴 SWE-agent 的 ACI「失败即反馈」原则与「何时停止」策略，补上 Tau 的两处真实缺口。
> 原则：P0/P1 相互独立、各自可交付；全部改动落在现有接缝上，核心循环（tau_agent/loop.py 的
> 轮次结构）一行不动；默认行为与现状完全一致（P1 全部策略默认关闭）。

---

## P0 · ACI 纠错反馈：坏 JSON 参数不再静默

### 现状与问题

五个适配器在工具参数 JSON 解析失败时兜底：

```python
arguments = _loads_object(arguments_text) if arguments_text else {}
if arguments is None:
    arguments = {"_raw_arguments": arguments_text}   # 静默兜底
```

后果：解析失败不产生任何信号；`_raw_arguments` 全仓零消费者（已 grep 验证，测试也零依赖）；
工具拿到缺 key 的 dict，报出误导性错误（如 `path must be a string`）；模型无法自纠。
违反 ACI 原则：接口应设计成「模型犯错时，反馈本身就能教它改」。

### 设计决策

1. **标记字段**：`ToolCall` 增加 `malformed_arguments_text: str | None`，用
   `Field(default=None, exclude=True)`——不参与序列化。
   理由：标记只服务于「适配器 → 循环」的同进程传递；持久记录由结构化反馈消息
   （ToolResultMessage）承担；不进序列化则旧会话可读、新会话不膨胀、无前向兼容问题。
2. **短路位置**：`loop.py` 的 `_execute_tool_call` 内、`before_tool_call` 门禁**之后**、
   工具查找之前。理由：门禁仍能看到这次调用（扩展可观察、可拦截）；事件形状完全不变
   （ToolExecutionStart → End → MessageStart/End），前端与持久化零改动。
3. **反馈内容**：结构化错误结果，引用原文，给出可执行的修正指令。
4. **删除 `_raw_arguments`**：无消费者，直接移除；解析失败时 `arguments = {}`
   （与 arguments_text 为空字符串的现有路径行为一致）。

### 改动清单

**1) src/tau_agent/messages.py** —— `ToolCall` 增加字段：

```python
class ToolCall(WireModel):
    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    arguments: dict[str, JSONValue] = Field(default_factory=dict)
    thought_signature: str | None = None
    malformed_arguments_text: str | None = Field(default=None, exclude=True)  # 新增
```

**2) src/tau_agent/loop.py** —— `_execute_tool_call` 增加短路分支 + 文案常量：

```python
MALFORMED_ARGUMENTS_MESSAGE = (
    "Tool call arguments could not be parsed as JSON. "
    "The raw text received was:\n<raw_arguments>\n{raw}\n</raw_arguments>\n"
    "Please correct the JSON syntax and call the tool again."
)
```

在 `_execute_tool_call` 的 `elif signal is not None and signal.is_cancelled():` 之后插入：

```python
elif call.malformed_arguments_text is not None:
    result = _error_result(MALFORMED_ARGUMENTS_MESSAGE.format(raw=call.malformed_arguments_text))
    is_error = True
```

**3) 五个适配器点**（各 3 行改动，模式一致）：

```python
# anthropic.py:415-417 / mistral.py:300-302 / openai_codex.py:392-394
# openai_compatible.py:726-728（chat builder）+ 780-782（responses builder）
arguments = _loads_object(arguments_text) if arguments_text else {}
malformed = arguments_text if arguments is None else None
if arguments is None:
    arguments = {}
# ...ToolCall(..., malformed_arguments_text=malformed)
```

**4) tests/test_malformed_tool_arguments.py**（新增）：

- 适配器单元：四个适配器的 builder 喂坏 JSON → `malformed_arguments_text` 有值、`arguments == {}`、不抛异常
- 循环集成：FakeProvider 回放带标记的 ToolCall → 断言 `execute_fn` **未被调用**；
  结果消息内容含原文与修正指令；事件序列形状与正常执行一致
- 序列化：带标记的 ToolCall 走 JSONL 往返 → 标记字段不出现、往返后消息完整

### 风险与边界

- 回放：坏调用 + 反馈结果在历史中是配对完整的（repair_tool_history 无需改动）；
  provider 重放时 `input = {}`（空对象），与现有空参数路径一致。若个别 provider 校验
  拒绝空 input，再在 `_provider_context` 侧扩展修复——不做预防性改动。
- 模型可能再次发坏 JSON：这是预期行为——反馈至少让它知道「问题在 JSON 语法」，
  而不是被误导去改字段类型。连续失败交给 P1 的连续错误阈值兜底。

---

## P1 · 运行策略层：预算与连续失败

### 现状与问题

Tau 知道「怎么继续」（重试、退避、压缩、溢出重试、路由 failover），但没有
「继续已经不值得了」这一层：数据齐全（`session_usage.py` 的用量统计、
`session.py:1350` 的 `_pricing_for_response` 定价），却无人拿它做运行期决策。
缺：单次运行成本上限、token 上限、连续错误轮次阈值、优雅放弃（总结进度交回用户）。

### 设计决策

1. **落层**：新模块 `tau_coding/run_policies.py`，作为 **harness 事件监听器**挂在
   CodingSession 上——与持久化监听器（session.py:3472）、扩展监听器（session.py:789）
   同款接缝。**不进 tau_agent**：核心循环只管「怎么走」（机制），「值不值得走」是
   应用层策略（随产品形态变化）。
2. **MVP 动作 = steer 注入**：阈值触发时调用现有 `session.queue_steering_message()`
   （session.py:1395），注入一条「预算/失败上限已到，请总结当前进度并停止」的转向消息。
   模型自己写收尾总结；前端通过**已有的** `QueueUpdateEvent` + 消息事件感知，零新事件类型、
   零前端改动。可选 `action="cancel"` 硬停（调用现有 `session.cancel()`）。
3. **默认全关**：`RunPolicyLimits` 三个阈值默认 None → 行为与现状完全一致。
4. **配置落点**：`~/.tau/settings.json`（shell_config.py 加载，`defaultProjectTrust`
   已住此文件），JSON 键 `runPolicies`；沿用「只读本版本认识的键」的前向兼容惯例。

### 改动清单

**1) src/tau_coding/run_policies.py**（新增，约 250 行）：

```python
@dataclass(frozen=True, slots=True)
class RunPolicyLimits:
    max_cost_usd: float | None = None          # 单次运行费用上限
    max_tokens: int | None = None              # 单次运行 token 上限
    max_consecutive_error_turns: int | None = None  # 连续错误轮次阈值
    action: Literal["steer", "cancel"] = "steer"

class RunPolicyMonitor:
    """会话级运行策略监听器：预算与连续失败阈值。"""
    def __init__(self, limits, *, queue_steering, cancel, price_response):
        ...
    def reset(self) -> None:
        """每次 prompt()/continue_() 前调用，清零本轮计数。"""
    async def on_event(self, event: AgentEvent) -> None:
        # MessageEnd(AssistantMessage)：
        #   stop_reason == "error" → 连续错误 +1（aborted 不计，那是用户取消）
        #   否则 → 连续错误清零；累计 usage（sum_usage）
        # TurnEndEvent → 检查三个阈值 → 触发则 queue_steering(引导语) 或 cancel()
        # 每个策略只触发一次（fired 集合），不重复注入
```

引导语文案（模型可见，同时用户从转录可见）：

```
Run policy exceeded: {reason}. Please stop working on this task, summarize the
progress made so far, and list any remaining work for the user.
```

**2) src/tau_coding/shell_config.py** —— settings.json 增读：

```python
@dataclass(frozen=True, slots=True)
class RunPolicySettings:
    max_cost_usd: float | None = None
    max_tokens: int | None = None
    max_consecutive_error_turns: int | None = None
    action: Literal["steer", "cancel"] = "steer"

# ShellSettings 增加字段 run_policies: RunPolicySettings | None = None
# to_json 写 "runPolicies"；from_json 只读认识的键，非法值报 ShellConfigError
```

**3) src/tau_coding/session.py** —— 接线（四处小改）：

- `__init__`/`load()`：构造 `RunPolicyMonitor`（limits 来自 config 的 ShellSettings 注入）
- `_attach_persistence_listener()` 旁边新增 `_attach_policy_monitor()`（同款 subscribe 模式）
- `prompt()` 与 `continue_()` 运行前 `monitor.reset()`（session.py:3189 附近）
- `aclose()` / harness 替换路径：退订（复用现有 unsubscribe 模式）

**4) tests/test_run_policies.py**（新增）：

- 连续错误：FakeProvider 回放 N 个 error 轮次 → 第 N 次后 harness 队列里出现引导语
- 费用上限：AssistantMessage 带 cost 的 Usage → 累计越过阈值 → 触发一次
- `action="cancel"` → 运行停止、后续轮次不再发生
- 每次 prompt 之间状态重置（第二次运行不再触发）
- 默认全关：无 limits 时监听器存在但永不动作

### 风险与边界

- 与溢出重试/AutoRetry 的交互：error 轮次会同时触发 overflow 压缩重试与连续错误计数——
  这是**正确**的：重试一次仍失败，连续计数自然到达阈值。不特殊处理。
- 定价不可用时（`_pricing_for_response` 返回 None）：费用策略跳过，token/错误阈值照常。
- `aborted`（用户取消）不计入连续错误。
- P1 不新增 SessionOwnEvent；若后续要「前端弹窗选择继续/收工」，再加一个事件类型
  （tau_coding/events.py 的 union 加一项 + TUI adapter 加一个状态字段），作为 P1.5。

---

## 验证步骤

```bash
uv run pytest tests/test_malformed_tool_arguments.py tests/test_run_policies.py -q   # 新增
uv run pytest tests/test_agent_loop.py tests/test_agent_harness.py tests/test_tau_ai.py -q  # 核心回归
uv run pytest -q                                                                # 全量（有把握后）
```

## 不做什么（明确边界）

- 不引入 Environment 抽象（P3 的体量，视批量评测目标另行立项）
- 不改核心轮次结构、不动 harness 队列语义
- 不做 TUI 专属 UI（MVP 复用现有事件渲染）
- 不新增依赖

## 提交边界

两条改动各自原子提交（P0：纠错反馈；P1：运行策略层），不混合无关修改。
未经水哥明确授权不执行 commit/push。

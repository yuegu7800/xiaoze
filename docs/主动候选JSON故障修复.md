# 主动候选JSON格式故障修复

**最新状态：2026-11-12又收到真实“空正文”反馈；进一步修复及验证见文末“空正文后续排查”。此前JSON参数修复不等于真实提供方验收通过。**

随后用户反馈（同日）：“测试可以”。因此最新状态为用户报告修复后候选路径可用；未获得实际正文或逐用途验收材料，不将其扩大为全部场景通过。没有自动运行或后台调用。

日期：2026-11-12。用户明确要求解决主动开口候选格式错误。仅修改本地候选请求、静态错误诊断及验证材料，没有启动微信、后台任务或真实模型请求，没有读取真实配置、Key、聊天库。

## 发现与改动

截图表明提供方的正文不能通过候选JSON解析。源码检查确认：提示词虽要求JSON，实际`requests.post`负载却没有`response_format`，与普通自由聊天使用相同传输模式。此前只兼容完整JSON代码框没有解决这一缺口。没有查看真实返回正文，不能断言该提供方的唯一故障原因。

`core.complete_chat_result`和`ApiCompletion`新增仅针对本次请求的`json_object`选项。原活动面板的模型候选分支启用它，向OpenAI兼容接口发送`response_format: {"type":"json_object"}`；私聊、群聊和公开素材草稿共用此路径。普通对话不带该字段；不修改运行时模型、地址、Key或保存配置格式，不新增依赖。现有系统指令含明确JSON协议和允许沉默的约定。

JSON模式约束格式，不保证字段或内容正确，原解析校验继续检查decision/text、重复字段、正文长度及完成状态，不从普通文字里强行提取草稿。错误现在区分重复字段和JSON解析失败；候选HTTP 400/422提示核对接口/模型的JSON对象模式和参数，不回显服务商正文，不把所有400都断定为不支持。拒绝、截断或格式错误不保存、不重试、不自动降级为普通文字。

## 实际验证

使用既有Python环境，在项目目录执行：

- `python -m unittest -q test_candidate_format test_activity test_character test_core`：56项通过。
- `python -m unittest -q test_candidate_format test_stage8_review test_stage8_feedback test_stage7_review test_stage7_evidence test_stage6_review test_stage6_recall test_stage5_review test_memory_library test_stage4_review test_dialogue test_stage3_review test_activity test_memory_v2 test_character test_core test_groups test_relationships test_moments test_receiving test_visual_adapter test_sessions`：234项通过，旧断言保留。新增3项分别覆盖模拟提供方格式模式差异、JSON模式失败不提交/不重试/不泄漏、合法沉默。
- `python smoke_candidate_json.py`：通过。全新临时目录、虚构资料，实际Tk候选按钮与mock HTTP验证私聊/全群/公开素材的JSON参数、草稿可选可查看、聊天输入保留、不自动采用、随后普通聊天没有JSON参数。
- `python smoke_dialogue_ui.py`：通过。原证据、确认话题、采用、接话、反馈、删除、绘制失败恢复和明确升级流程继续有效。
- `python launch_local.py --check`：通过，导入不读取配置或加载微信执行层。

Tk脚本在系统用户上下文运行以访问系统Tcl；所有资料仍在临时目录，HTTP被mock，没有真实计费。

修复前复现使用同一个虚构接口函数：没有JSON参数时返回普通聊天正文，原调用失败且文件/内存不变；有参数时返回合法JSON，单次请求保存为未发送草稿。该对照证明本地请求路径修复，不代表真实提供方已经验收。

## 用户下一步与状态

关闭旧窗口，通过原`启动小泽本地试聊.cmd`重开，确认已保存的配置填入后选择模型试聊。在原有效话题场景点击一次“评估一次”。合法draft会出现在草稿列表，点该行查看；合法silent会说明沉默和10分钟冷却。真实评估可能计费，没有自动重试。已存在待处理草稿、冷却或边界阻止时，可能根本不发起请求。

已实现：候选JSON传输与诊断修复。虚构模拟通过：网关/事务回归及实际Tk按钮路径。真实模型验证：本轮未进行，需要用户手动确认。仅设计/未开展：自动JSON修复、供应商能力探测、自动重试、后台主动调度及微信执行。

## 空正文后续排查（2026-11-12）

用户截图底部出现“模型返回空正文，已暂停”，用户随后确认填写模型ID为`deepseek-chat`，认为对应4.1 Flash。截图只能证明旧网关的空正文检查被触发：不能据此区分null、空字符串、仅空白、输出截断、只有推理或工具调用。未读取配置文件、Key或真实响应正文，没有调用真实/付费API。

本轮读取的公开官方来源：

- [JSON Output](https://api-docs.deepseek.com/guides/json_mode)：明确要求JSON对象参数、提示中json词与格式示例、合理max_tokens；并说明不合规JSON可能被替换成U+0020空格，甚至finish_reason=stop时也会表现为空白。
- [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)及[Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion)：当前默认启用思考，支持`thinking.type=disabled`；推理与最终正文分别在reasoning_content/content，不能混作回复。
- [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)：本轮页面列Flash版本为DeepSeek-V4.1-Flash-1017、推荐模型名deepseek-flash。当前页面没有据此确认deepseek-chat实际映射，未替用户换模型。官方现行默认也不能证明用户的旧别名一定采用同样默认。

修复前相关基线234项通过。新虚构复现发现：带length结束状态但正文为空、只有推理、仅空格、拦截等都被遮蔽成“空正文，已暂停”；新3个测试函数首次运行出现9个失败断言和1个错误。候选窗口实际错误分支只设置last_error，没有设置app.paused或core._paused，因此“已暂停”的文字本身也不准确。

本轮实际改动：

1. 候选协议明确沉默必须返回`{"decision":"silent","text":""}`，不能用空白表示；明确历史助手的普通聊天正文不是本次输出格式示例，普通排版要求适用于text字符串。三种候选路径在最终用户JSON中增加固定output_requirement，不把这些任务指令写进记忆、话题或原文，不改人格。
2. 官网域名严格匹配`api.deepseek.com`且为JSON候选请求时，显式发送`thinking={"type":"disabled"}`，用于短候选决策，避免依赖当前默认思考模式。其他域名不会因此收到DeepSeek专有参数；已有显式send_thinking_disabled/legacy行为仍按原调用语义。普通聊天不因本修复切换模式。模型ID、API地址、Key和加密保存配置格式均未改。
3. JSON候选输出上限从1024改为2048 token，为JSON封装和最长800字正文留余量；普通聊天仍1024，记忆与来源预算不变。上限增加可能增加单次最多输出费用，界面已提示；仍每次用户点击最多一次请求，无自动重试、自动降级或第二次润色。
4. 先验证finish_reason再诊断空正文，UI只显示白名单状态：结束原因、正文是缺失/null/空字符串/仅空白、reasoning_content是否有内容。区分输出或上下文达到限制、拦截、工具状态、只有推理、普通空正文与空JSON。未知状态不回显原值，非文字正文明确类型错误。状态解释不证明真实故障唯一原因，DeepSeek空白时仅提示官方列出的可能性。
5. 推理内容不显示、不当作候选、不持久化；没有新日志/全文请求/失败原文/缓存或诊断字段。失败不提交草稿、不启动冷却，不声称暂停，不自动调用第二次；用户明确暂停及迟到取消沿用原流程。

验证命令：

```text
python -m unittest -q test_candidate_empty test_candidate_format test_activity test_core test_memory_v2
python -m unittest -q test_candidate_empty test_candidate_format test_stage8_review test_stage8_feedback test_stage7_review test_stage7_evidence test_stage6_review test_stage6_recall test_stage5_review test_memory_library test_stage4_review test_dialogue test_stage3_review test_activity test_memory_v2 test_character test_core test_groups test_relationships test_moments test_receiving test_visual_adapter test_sessions
python smoke_candidate_json.py
启动小泽本地试聊.cmd --check
```

结果：相关66项通过，完整237项通过，原断言未删改。Tk模拟验证私聊/全群/公开候选、后续普通聊天；另验证官网候选返回空白时诊断可见、文件不变、输入保留、没有自动重试/暂停/冷却，用户显式再次点击后才发第二次请求并创建草稿。中文启动器检查通过。Tk在系统Tcl环境运行，所有配置和资料虚构、临时存储、HTTP mock。

状态：代码和诊断修复已实现；虚构单测/实际Tk控件路径通过；真实用户提供过失败截图，修复后的真实API效果尚未验证。官方文档说明空JSON仍可能发生，单次无重试策略下不能保证每次生成成功。用户重开窗口，继续原模型配置，点一次“评估一次”；若失败，新提示给出实际返回状态，可据此继续定位，不再把空白当沉默。第九阶段之外的新能力、自动重试、微信操作与后台主动调度均未开展。

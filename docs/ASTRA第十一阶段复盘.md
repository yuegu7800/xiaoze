# ASTRA第十一阶段复盘与局部优化

日期：2026-11-12。用户要求复盘和优化第十一阶段，并准备第十二阶段提示词。实际目录为`C:\Users\ZhuanZ\Documents\Codex\wechat-acheng`。本轮已检查第十/十一阶段交付、自动策略、Tk集成、候选/采用事务和相关测试；没有读取正式配置、Key、聊天/人物库或既有`.local-xiaoze`。项目仍不是Git仓库，未初始化或发布。

## 结论

第十一阶段解决了有证据的自动任务异常清理问题，正常生成与采用的主要事务边界合理：自动任务复用原请求槽，提交凭据来自同一次候选保存，采用前核对具体draft_id和取消资格；请求次数不因失败/沉默退回，停止不伪装HTTP撤销。无需推翻第十阶段实现，也不需要再开展一轮无范围的异常框架重构。

本轮发现并修复同类缺陷在两个手动入口中的遗漏：手动聊天和手动候选先设置busy、禁用控件，再调用Thread.start；启动失败时没有工作线程会把完成事件送回，因此busy永久残留，后续手动操作被挡住。自动线程入口已在第十一阶段处理，不能据此推断手动入口也恢复了。

修复前用实际Tk和虚构资料注入Thread.start的RuntimeError，两个用例都失败在“busy仍为True”的行为断言。未将缺少新接口、导入错误或环境问题算作产品故障。

## 本轮改动

- `local_chat.py`：手动发送的控件准备、状态绘制、线程启动置于启动异常边界。失败释放busy、停止已有自动会话、保留输入/原记录，显示固定原因；状态栏不能绘制时仍独立尝试恢复发送控件。不调用Core.pause，不自动重试，不显示异常原文。
- `activity_panel.py`：手动候选使用同样的启动失败清理，活动面板提示、主窗口提示和控件恢复独立尝试。没有模型请求时不生成草稿、冷却或自动请求次数；用户之后可明确点击重试。
- `smoke_astra_stage11_review.py`：3条测试函数，其中启动前绘制故障覆盖两个入口。实际验证故障后输入保留、文件不变、请求槽空闲、自动已关闭、次数不增加，以及用户再次手动操作后正常生成；不只是检查内部标记。

保持原人格、关系、来源删除、召回、配额、默认时机、JSON协议、API配置和存储格式。没有新增依赖、持久诊断或自动模型调用。失败清理中的“未请求模型”只用于尚未成功启动工作线程的边界，不用它描述已发HTTP超时或已提交的绘制失败。

## 本轮实际验证

Python为既有虚拟环境`C:\Users\ZhuanZ\Documents\ChatGPT\奇思妙想\work\wechat-lab\.venv\Scripts\python.exe`，在项目目录执行：

```text
python -m unittest -q test_stage11_review test_auto_opening test_candidate_empty test_candidate_format test_stage8_review test_stage8_feedback test_stage7_review test_stage7_evidence test_stage6_review test_stage6_recall test_stage5_review test_memory_library test_stage4_review test_dialogue test_stage3_review test_activity test_memory_v2 test_character test_core test_groups test_relationships test_moments test_receiving test_visual_adapter test_sessions
python -m unittest -q test_local_api_profile
python smoke_astra_stage11_review.py
python smoke_stage11_review.py
python smoke_stage10_auto.py
python smoke_candidate_json.py
python smoke_dialogue_ui.py
启动小泽本地试聊.cmd --check
```

结果：修改前普通基线260项通过；修改后同260项通过（4.920秒），配置加密另3项在系统用户上下文通过（0.172秒），合计复核原263项，不把新增Tk用例混加为单元测试数量。新增Tk首批2项修复前均失败，最终3项全部通过（0.872秒）；原第十一阶段6条Tk测试和第十阶段自动路径、候选JSON、原对话/采用脚本通过。启动器通过；另在干净进程确认导入不新增线程、不加载app/wxauto/pywechat/uiautomation，AutoOpening默认关闭。

Tk/DPAPI需要系统用户上下文，所有数据仍来自新临时目录，HTTP mock；没有用真实资料绕过环境限制。最后改动是让手动发送的错误提示绘制与控件恢复独立执行，新增绘制故障用例及相关候选/对话脚本随之重验。不是重复跑全部历史Tk脚本来凑覆盖。

## 仍缺什么

1. **自动等待不够直观。** 当前status主要拼接note和次数；due每次检查会更新计时和note，等待时只说“10分钟”，没有剩余时间。达到小时/会话上限的提示也需要精确区分。用户容易将正在等待误认为程序没有运行。
2. **本地门槛与模型结果需要更容易读懂。** 原事务已区分没有话题、待处理草稿、冷却、合法silent及失败，但界面没有稳定的结构化当前状态，不能把随时被覆盖的一行note当历史执行证据。应优先改善当前状态说明，不默认再造诊断档案。
3. **当前能力仍依赖人工确认话题。** 自动开启不等于自动发现兴趣或从任何对话无限创造话题；这应在体验说明中讲清，不为让她“总有话说”绕过门槛。
4. **缺少第十/十一阶段的真实自动体验反馈。** 已接受用户对第九阶段的验收，但没有材料证明默认10分钟、夜间规则、配额和未回应门槛在实际长时间使用中都合适，也没有样本支持进一步改persona。保留这个未知，不要求用户重验旧阶段。

不建议马上新增微信群发送、全天自主生活、跨场景共享或新记忆基础设施。下一阶段应是一个小范围的“自动开口状态与体验闭环”：在原窗口看懂为什么等待、最早还需多久，继续原本地模式体验；有真实问题才做相应调整。具体任务见`docs/第十二阶段开发提示词.md`。

## 状态和交接

| 类别 | 本轮结论 |
|---|---|
| 已实现 | 补齐手动聊天/候选启动异常的清理与控件恢复 |
| 虚构模拟通过 | 原263项回归分批通过，新增3条Tk及相关原Tk/启动/导入检查通过 |
| 用户真实验证 | 第九阶段已有用户验收不变；本轮优化与自动频率/语言效果未做真实模型验收 |
| 仅设计 | 第十二阶段状态可读性与少量真实体验任务；没有执行该阶段 |

当前停在第十一阶段复盘交还。下一次执行者先读本文件与第十二阶段任务书，以当前源码为准，不覆盖T3已有实现，不重复旧阶段，不自行扩大范围。

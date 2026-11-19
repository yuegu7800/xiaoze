# A批次说明

A 已实现但尚未运行验证。主入口仍是 `启动小泽微信.cmd`，保留 app、PrivateRunner、GroupRunner、MultiRunner 的原群/私/双会话、连续回复、参与策略/配额/冷场主动及配置/关系历史。单次私聊入口只作诊断，不恢复为主入口。

`Conversation.incoming()`先检查原始整批JSON结构（不经过旧parse_batch的筛选/截尾）、目标、最多8条、每条显示名与非空正文、每条/总4000字符及128人物容量。`resolve()`只解析ID，不登记；返回的Incoming携带独立people候选视图。核心组装只在线程局部使用该Scene视图，正式people、identities、wechat_people、last_person在预览、非法批次、生成失败和取消时均不改。不存在整库回滚覆盖并发维护。

接受时点有两种：正式live观察/沉默路径在本轮许可仍有效时保存用户原话（reply为空）并接受人物；正常回复在外部发送操作后、本地核心提交的同一临界段接受人物和来源。只恢复保存失败时的窄人物元数据，不恢复整份core.state。旧资料bootstrap仍是原运行时一次性兼容逻辑，本批不扩展其容量/迁移策略，留D。

`CharacterCoreBase.prepare_exchange()`仍返回(reply, callable)，但callable现为有界PreparedExchange句柄，兼容check_current()并新增幂等discard()。从生成到待发送再到commit/discard终态持续占用；调用方必须最终释放。普通exchange仍立即提交。普通feedback在生成或待发送占用期间拒绝且不改变epoch；资料维护/删除与到期检查仍能使准备失效。核心锁只包围组装/验证和本地提交，不跨模型/原生传输IO。

完整runner的finally丢弃未提交句柄，模型异常/静默/抑制/预览/暂停/关闭/替换也有释放路径；旧句柄重复discard不能释放另一轮句柄。诊断PrivateBridge已在finally释放，避免预览或取消泄漏占用。IntegratedModel.validate_pending缺少准备结果时明确拒绝发送，观察/沉默不调用发送验证。

runner在读前捕获epoch/mode形成不可恢复的本轮许可；received/busy通知后、observe前、首次Conversation构造前后、实际completion前和发送门槛检查。暂停再恢复即使enabled重新为真也不能复活旧轮。IntegratedModel通过仅其支持的可选round_permission接收许可，不给旧ChatModel/GroupChatModel增加必需参数。单runner的非阻塞执行门防重入，取消操作不等待该门；模型状态锁不跨构造、模型、标题或发送IO。

原ScopedTransport两次can_send及RoutedTransport路由后的标题核验沿用，集成发送门同时验证固定轮次和句柄/来源有效性。进入原生send_text后不能承诺撤回；False/异常不重试，异常包括后端ScopeError都按不确定处理。完整版True仍表示执行发送操作、沿用连续流程，不证明收到/已读，不改成每条强制暂停。发送后核心提交失败与核心已保存但旧归档失败保持不同提示，均不重发。

新增 `test_wechat_batch_a_static.py` 是可执行的unittest回归源码（不是空规格），覆盖整批非法/容量无污染、失败/预览/静默、准备期间评价、释放所有权、维护失效、通知取消再恢复、Event协调首次构造取消、观察许可、completion回调门槛、路由前台检查与双会话连续回复/发送中反馈。原测试和断言未删除，新增回归全部未运行。`core.py`、`character_service.py`、`wechat_character.py`、`wechat_private_bridge.py`及新回归只做语法静态检查；B/C/D仍待后续，E才执行mock/Tk/启动验证。

本轮未运行测试，依用户要求留到最终。未启动微信/真实API、未读取Key/正式配置聊天人物库或既有.local-xiaoze，未修改运行时模型或接力控制。真实微信兼容、发送效果和语言自然度均未验证。318项等旧结果不代表本批通过。

实际静态命令：使用既有venv Python对 `character_service.py core.py wechat_character.py test_wechat_batch_a_static.py` 执行过 `-m py_compile`（实现中间状态，成功）；最终对上述文件加 `wechat_private_bridge.py` 读取源码并逐个 `ast.parse(..., filename=...)`，输出 `AST syntax OK (5 files); source parsing only, no business imports or tests.`。这两项仅检查语法，不是启动检查或测试。源码复核使用限定文件的rg/Get-Content，未读取正式运行数据。

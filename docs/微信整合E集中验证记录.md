# E集中验证记录

2026-11-13，r000008-implement-69ca59fd1e。A—D先实现/静态审查，E首次集中执行本批验证。最终代码完整351项单测通过，指定临时Tk、纯启动和干净导入通过。均为自动/mock结果，不代表真实微信或模型自然度验收。

解释器：`C:/Users/ZhuanZ/Documents/ChatGPT/奇思妙想/work/wechat-lab/.venv/Scripts/python.exe`。以下`python.exe`均为该解释器，工作目录为业务项目。无新增依赖、系统设置、运行配置/persona/昵称/路径或接力控制变更。

## 边界核对

已阅读policy、HANDOFF、批次方案/台账及A—D说明，限定查看测试、启动器和必要源码。测试场景均TemporaryDirectory、虚构身份/聊天及mock completion/transport；HTTP测试用mock响应，虚构DPAPI回归仅处理其当次新建临时资料。Tk完整App通过临时DATA/虚构配置、mock加解密和mock后端模块运行，不加载实际后端。LocalChat指向新临时目录并使用模拟或mock模型。没有运行smoke_group_api.py或正常真实启动。

旧test_visual_adapter.py原来会import wx_backend。为满足本轮禁止加载后端，改为从已知旧源码只AST提取analyze_blocks和get_messages函数定义，隔离命名空间、延迟注解、mock像素/OCR依赖；get_messages仅检查函数复制合同、不执行收件。其五项原断言均保留；无外部源码修改、无后端导入/实例化、无桌面截图/OCR/按键。空白合成图几何检查的范围不等于真实视觉验收。

## 实际命令与结果

| 命令 | 实际结果 |
| --- | --- |
| `python.exe -B -m unittest test_wechat_batch_a_static test_wechat_batch_b_static test_wechat_batch_c_static test_wechat_batch_d_static test_wechat_character test_core test_groups test_sessions test_receiving` | 首轮112项，2.030秒，OK，退出0；其中A/B/C/D为9/6/9/6项 |
| `python.exe -B -m unittest discover -p 'test_*.py'`（首次） | 348项，7.296秒，退出1，4 errors：旧UI夹具缺turn_id，以及隔离几何依赖drop_panel_contents缺失（3项） |
| `python.exe -B -m unittest test_stage5_review test_visual_adapter` | 补齐测试夹具稳定绑定/people映射与隔离依赖后12项，0.204秒，OK，退出0 |
| `python.exe -B -m unittest test_wechat_batch_e` | 新增3项，0.565秒，OK，退出0；首次暂存不能冒充主动实际发言，live观察原子接纳后才有新前情；受保护干净导入与--check |
| `python.exe -B smoke_wechat_batch_d.py` | 初版退出0；补原话滚动失效检查后最终退出0，打印D temporary Tk PASS；新成员/重名、预览归属、删除和滚动后不转绑、编辑保留、断开回调清理 |
| `python.exe -B smoke_wechat_character.py` | 初次退出0；补实际旧窗口按钮同群导航及不存在目标检查后最终PASS，退出0；双会话、稳定反馈、维护、话题、新成员及断开清理 |
| `python.exe -B smoke_wechat_continuity.py` | PASS，退出0；原私/群/双三模式、保存配置、原模型/昵称、旧绑定和朋友圈工具，HTTP调用0 |
| `python.exe -B smoke_dialogue_ui.py` | PASS，退出0；原话证据、话题/采用、来源删除、反馈、已提交绘制失败恢复及v2显式升级 |
| `python.exe -B smoke_stage7_evidence.py` | PASS，退出0；精确turn、固定Core/场景、到期失效、可滚动证据与反馈提交一次 |
| `python.exe -B smoke_local_ui.py` | 补核共用MemoryPanel的原本地路径，GUI及Stage3 PASS，退出0；候选确认/来源删除、输入保留、隔离练习、话题与mock复制 |
| `cmd.exe /d /c '"C:\Users\ZhuanZ\Documents\Codex\wechat-acheng\启动小泽微信.cmd" --check'` | `Startup imports: OK. No window opened; no WeChat connection.`，退出0；静态确认cmd跳check分支，不启动pythonw/App |
| `python.exe -B -m unittest discover -p 'test_*.py'`（最终） | **351项，7.921秒，OK，退出0**；含全部原发现范围与新增E回归，最终测试源码状态 |

首跑错误都在测试适配层：D新增稳定绑定后旧`MemoryPanel.__new__`夹具需明确turn_id/mark_bound_id与people，原“绘制失败后提交一次/保留提示”断言未删；隔离函数依赖用mock补齐。没有放宽业务边界、删旧断言或以历史结果替代。A—D本轮未暴露需改动业务实现的动态失败，未声称穷尽所有并发或真实后端行为。

## 重点验证范围

A覆盖非法批次/新人物无污染、失败/预览/沉默、准备到提交/丢弃占用与反馈隔离、维护使准备失效、received/busy通知取消再恢复、首次构造Event取消、completion前及路由/标题后许可、双会话连续与发送中反馈。旧诊断桥接回归仍含收件/检查阻塞取消、底层发送异常不确定及不重发。

B覆盖前位跨窗口边界、分人同槽临时覆盖/到期/抑制、同源去重、共享6条/1800字符/完整来源拒绝、个人话题目标/撤回依赖、群私/跨群隔离和正文不提权，进程内证据无正文。

C覆盖重载实际人物而非manifest/profile顺序、有效个人/all话题、not_before/修改/删除/到期/取消、全受众安静不被有限取数绕过、单/多人群、零或一次completion、沉默不发送/不提交/不增发送配额、不要求本地候选前置和真实来源祖先。主动事件不伪造本轮用户文本/时间/来源，不补普通轮证据。

D覆盖128人三偏好及历史/profile并集、非法行/批次/profile/preferences、主人别名合并与固定规则、暂存/模型失败/写失败/导入中断、重载/旧布尔标记、删除/重置不再导入或补空位、unknown-time与legacy_review删除祖先。旧归档原件保留，未导入数量在同一核心界面可见。E补跨A/C/D首次暂存边界，Tk用例实际操作正确群导航和稳定维护选择。

## 启动和导入证据

E三项回归包含两个干净子进程：导入app/wechat_character/wechat_character_panel/wechat_private_bridge时，拒绝Path.read_text、mkdir与Tk窗口构造，拒绝wx_backend导入，断言cwd/环境/sys.path完全不变。另一个子进程在同类文件/窗口保护下执行launch.main的--check，确认不打开窗口、不读取资料、不加载后端。真实cmd检查仅执行安全--check，不以正常启动替代。所有检查实际通过，无工具/权限未解决阻塞。

## 用户手动体验与实际限制

1. 用原完整版入口核对配置和目标，先连接预览；不要同时启动独立诊断版。
2. 私聊和群分别用新的虚构偏好/边界检查归属，再由用户自行明确启用实际回复。
3. 对应会话进入核心窗，核对首次兼容暂存/已持久化和未导入计数，选稳定轮反馈；试一次更正/删除。旧档窗口负责绑定/原归档，导航不负责连接。
4. 停止用暂停、断开或关闭。已进入原生发送的按键不能承诺撤回；“发送操作已执行”不等于送达/已读，失败/不确定不自动重试或重发。

真实微信视觉/账号识别、接收/发送兼容和语言自然度均未验证。显示名不认证账号；原资料副本仍保留，核心删除不是全副本擦除；无可靠实际原话时主动会沉默。用户可自行体验并仅提供愿意分享的少量不自然片段。完成交付后结束本轮，不新增阶段、不派发、不读取/推测控制器状态。

# 表情与梗项目评估：小泽与 ChatLab

核查日期：2026-11-11。结论：值得借鉴，优先是按需查梗、范围隔离和可弃选的表达资源；不适合直接安装三个插件来替换现有角色核心。本文是研究建议，不是第八阶段或表情功能的实现记录。

## 核查范围

阅读公开仓库的 README、许可证与下述相关代码；未安装依赖、运行上游程序或验证其运行效果。三个插件核查时均未归档且有近期提交，这只能证明近期有维护活动，不能证明稳定性。公开源码缓存位于 `.research/meme-plugins-20261111`，不含本地真实聊天或人物资料。

本地对照为小泽 `ASTRA第七阶段复盘.md` 与尚待执行的 `第八阶段开发提示词.md`。用户确认“梳理关系那个”指 ChatLab / 人物关系图谱。ChatLab 对照的是当前公开上游，不表示已审查用户本地改版；没有读取真实聊天、Key、旧配置或既有 `.local-xiaoze`。没有微信操作、真实模型请求、上传或发布。

| 项目与固定提交 | 核查时最近推送（UTC） | 代码许可 |
|---|---|---|
| anka-afk/astrbot_plugin_meme_manager `07acb24c6b7b3c2d1f092c1668a405eb3540e066` | 2026-11-11 | MIT |
| PurLango/astrbot_plugin_memewiki `158431e7f8474dc68e268e9c0f4cc4ae4e5319e6` | 2026-11-09 | MIT |
| yyh-001/dsh-meme `19db20dc1346fdeaf54aad576b5740c1815a8423` | 2026-11-11 | MIT |
| ChatLab/ChatLab `c258a139d323e0743ff06d82d915a726d1c2a733` | 2026-11-04 | AGPL-3.0 |

原 hellodigua/ChatLab 地址当前重定向至 ChatLab/ChatLab。许可证已对照仓库实际文件；图片和词条内容的权利需与代码分别处理。

## 1. astrbot_plugin_memewiki：优先借鉴按需词义参考

实际 `main.py` 将自定义黑话按 `group:<group_id>` / `private:<sender_id>` 分隔，先查当前范围，再查公共词库；支持精确标题、别名及若干片段匹配。自动预查主要针对问梗句和短词，命中后临时注入一条词条，不将整个词典塞进每轮。资料提示明确词条不是指令、不能改变身份或权限，查不到不编造。

对小泽：有助于理解群内黑话和缩写，避免逢梗解释或乱猜；资料只作为本轮语言参考，不自动形成用户偏好、关系或共同经历。对主人温柔原则不能被“串子程度”覆盖；不建议复用全局调侃强度开关。

对 ChatLab：适合作为选中消息后的“可能词义 / 来源 / 适用群”注释。不能凭一条词义直接推断说话者意图。公共词义和群内约定应分开，同一词在不同群允许有不同解释。

成本与限制：普通词典查询是本地操作，没有额外识词模型；预注入增加主请求 token，模型主动工具查询可能增加主模型往返。手动更新词库会访问外部网站。现有范围键只用群/用户 ID，若借入多平台或多机器人项目，应加平台与账号命名空间，不能直接视为足够隔离。片段匹配取单个结果不代表可靠消歧，适合保守借鉴，不照搬成小泽的新记忆召回。

README 声明附带 6792 条词条；本次没有打开数据库逐条核验。NOTICE 说明快照来自 dsh-memewiki、原插件及 cnmeme.wiki，内容版权归原作者及来源平台。MIT 代码不等于整库内容获得统一 MIT 授权。初期宜使用自行编写且明确来源的小词表。

来源：[README](https://github.com/PurLango/astrbot_plugin_memewiki/blob/158431e7f8474dc68e268e9c0f4cc4ae4e5319e6/README.md)、[实际匹配、范围与注入](https://github.com/PurLango/astrbot_plugin_memewiki/blob/158431e7f8474dc68e268e9c0f4cc4ae4e5319e6/main.py)、[NOTICE](https://github.com/PurLango/astrbot_plugin_memewiki/blob/158431e7f8474dc68e268e9c0f4cc4ae4e5319e6/NOTICE)、[LICENSE](https://github.com/PurLango/astrbot_plugin_memewiki/blob/158431e7f8474dc68e268e9c0f4cc4ae4e5319e6/LICENSE)。

## 2. astrbot_plugin_meme_manager：借鉴候选选择与投递分离

实际 `backend/selection.py` 区分选中、主动放弃和失败，限制输入与候选 ID，禁止编造图，明确区分安慰/嘲讽、自嘲/指责别人。`backend/delivery_history.py` 只为已确认投递保留有限会话记录，默认最多128会话、每会话10项、1小时有效；不保留消息正文或图片路径。这与小泽“候选不等于已执行”的边界相容。

对小泽：可借鉴表达候选结构、人物/场景选包、允许沉默和文字独立成功。真正使用时，应先有小泽自己的关系与表达决定，再在允许的资源中选图；不能用表情情绪标签反过来随机切换人格。尚无依据直接抽出完整 selection 模块，它依赖决策后端；优先借鉴小接口和验证规则。

成本与限制：这是 AstrBot 插件，README 要求 AstrBot 4.28.2+ / Python 3.12+，不是当前 Tk/Python 程序的即插即用模块。分类模式可不增加额外选图模型调用；视觉描述、embedding、辅助选图及自动收集另有请求与数据流。requirements 包括 faiss-cpu、minio、pillow 等，不适合为少量本地表情整体引入。

特别注意：README 当前说明 `send_meme(target)` 主动发送工具默认开启，且不受自动配图概率与数量上限限制；将自动概率设为0并不等于完全禁止发送。自动收集默认关闭、开启后默认人工审核。当前本地阶段不引入发送工具、自动收集、云同步或语义索引。

对 ChatLab：管理描述、分类、指纹和复核流程可以参考；实际发图工具对关系分析没有必要。图片来自何人、是否适合公开，与图库可检索是不同问题。

来源：[README](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/07acb24c6b7b3c2d1f092c1668a405eb3540e066/README.md)、[selection.py](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/07acb24c6b7b3c2d1f092c1668a405eb3540e066/backend/selection.py)、[delivery_history.py](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/07acb24c6b7b3c2d1f092c1668a405eb3540e066/backend/delivery_history.py)、[LICENSE](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/07acb24c6b7b3c2d1f092c1668a405eb3540e066/LICENSE)。

## 3. dsh-meme：借鉴本地选图体验，不替换宿主

这是 DeepSeek Harness 插件，不能因名字含 DeepSeek 就理解为仅支持某个模型的独立机器人，也不能通过填入 DeepSeek Key 直接接入现有小泽。

README 和 `index.js` 提供输入框选图、图库筛选、预览、文字标记与平台图片投递分开、显式收藏后识图入库。`memes.js` 实际检索是描述/关键词/文件名的词项或片段匹配，同分候选随机抽取；不是关系推理或通用语义理解。

对小泽：最有价值的是“先选入输入框、用户确认后再采用”的交互，以及按真实候选解析资源标记的思路。初期只做本地预览，资源用稳定 ID，不让模型生成任意本机路径。对 ChatLab：可参考图片描述与人工修订 UI，但不需要聊天发图外壳。

成本与限制：依赖 DSH 的工具、附件、模型与 Web/IM 服务；要求 Node 22.13+，与当前 Tk/Python 宿主不同。核心声明零第三方运行依赖不代表无需宿主，QQ缩图另有可选 sharp。`learn_meme` 未指定分类时调用识图模型，外部 imageUrl 会下载图片；不能作为当前阶段的零成本自动学习。README 将代码 MIT 与内置/市场图库图片权利明确分开。

来源：[README](https://github.com/yyh-001/dsh-meme/blob/19db20dc1346fdeaf54aad576b5740c1815a8423/README.md)、[index.js](https://github.com/yyh-001/dsh-meme/blob/19db20dc1346fdeaf54aad576b5740c1815a8423/index.js)、[memes.js](https://github.com/yyh-001/dsh-meme/blob/19db20dc1346fdeaf54aad576b5740c1815a8423/memes.js)、[LICENSE](https://github.com/yyh-001/dsh-meme/blob/19db20dc1346fdeaf54aad576b5740c1815a8423/LICENSE)。

## ChatLab / 人物关系图谱具体如何受益

当前上游已有关系全景、群内互动与私聊统计，并有斗图榜 UI。实际关系图计算依据回复互动、共同出现及时间等；私聊统计包括发起、收尾、破冰与响应时延。它们是互动指标，不是心理好感的测量。无需为这三个插件再搭一套关系图。

可以增加的只是解释层：用户选中一条消息，查看候选词义及该范围约定，结合前后文人工确认。若导入记录只有“[表情]”占位，没有图片或可辨识描述，应明确无法识别，不能由词典补造图义。

例如虚构成员发“小丑”图：它可能是自嘲、引用或讽刺别人。词典不能决定指向谁，更不能据此永久标成敌意。图谱中“经常共同出现”也不能自动变成“小泽与该人熟悉”。

两项目可共享有授权的公共词典/素材规范与查询设计；默认不共享真实私聊、群内黑话、关系结论或完整人物库。未来如果用户确有需要，只让其逐条确认可用的偏好或称呼，并保留来源/范围；这是建议，未实现。

ChatLab 是 AGPL-3.0。MIT 小模块原则上可在保留声明的前提下纳入 AGPL 项目；反过来直接复制 ChatLab 代码到小泽，需要先评估整体许可义务，不能把它当 MIT 使用。不同应用交换用户明确允许的数据与复制代码是不同问题。

来源：[ChatLab README](https://github.com/ChatLab/ChatLab/blob/c258a139d323e0743ff06d82d915a726d1c2a733/README.md)、[图谱计算](https://github.com/ChatLab/ChatLab/blob/c258a139d323e0743ff06d82d915a726d1c2a733/packages/node-runtime/src/services/people/relationships/compute.ts)、[私聊统计](https://github.com/ChatLab/ChatLab/blob/c258a139d323e0743ff06d82d915a726d1c2a733/packages/core/src/query/advanced/relationship.ts)、[斗图榜](https://github.com/ChatLab/ChatLab/blob/c258a139d323e0743ff06d82d915a726d1c2a733/src/components/analysis/ranking/sections/MemeBattleRank.vue)、[LICENSE](https://github.com/ChatLab/ChatLab/blob/c258a139d323e0743ff06d82d915a726d1c2a733/LICENSE)。

## 与小泽当前进度的关系及建议顺序

1. 保持第八阶段原定的逐轮反馈定位和中文证据摘要。当前没有真实语言样本支持先扩充人格或大型语义服务。
2. 待单独授权后，可做一个小型本地试验：少量自编公共词义、群内词义隔离、显式查词或保守按需参考、未知不猜。检查误注入、身份规则覆盖及跨场景泄漏；词义资源不能直接灌入人物事实库。
3. 用户确有表达需求时，再加入少量权属明确、人工描述的本地表情，先手动挑选/预览；后续才评估同轮候选选择、允许不选和重复控制。不要额外自动付费选图，不开始微信投递。

这些项目能补“理解网络表达”和“表达资源的选择”，不能直接补齐稳定人格、选择性遗忘、可信关系、主动聊天的分寸或实际微信执行。三个插件的“主动发图”也不等于小泽独立决定何时开始一段新对话。

状态：公开源码与许可证研究已完成；本文建议仅设计；未复用代码、安装插件或改变小泽运行时；未验证真实语言效果或图片投递。

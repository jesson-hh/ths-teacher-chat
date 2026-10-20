# 桌面程序内的老师消息读取

`desktop-source.js` 由自己的 WebView2 宿主在官方目标页面 `NavigationCompleted` 后注入。用户在该程序自己的原生网页中登录；脚本不处理登录信息，不依赖浏览器扩展、本机配对码或 HTTP 桥。

输出调用固定为：

```javascript
window.chrome.webview.postMessage(JSON.stringify(payload));
```

宿主用 `TryGetWebMessageAsString()` 得到 JSON 字符串，再严格校验。payload 与本机 feed 的 DOM 合同一致：`identity_mode:'dom_teacher'`、`room_id:'yuen-route:VzPD.Y8fIGs_'`、`teacher_id:'dom-teacher:VzPD.Y8fIGs_'`、`source:'yuen-native'`、`observed_at`、`mode:'snapshot'|'live'`、`source_ready:boolean`、`messages`。

每条消息必需字段为 `id`、`author_id`、`role`、`public:true`、`published_at`、`text`、`images`。原生专用 `allow_public_chat` 开关允许 payload 携带 `view_mode:'teacher'|'all'`，并允许消息和 `reply_context` 携带 `display_name`（原站公开昵称、最多 80 字符）、`source_id`（原站消息编号、最多 256 个白名单字符）。默认 teacher 视图只接受 teacher；all 视图接受 teacher/student/assistant，作者键为固定合成角色 scope，不导出真实账号 UID。`reply_context` 仍须有 `text`（最多 4000 字符）、`images`（最多 1 图）、`published_at`（带时区时间或 null）。HTTP/扩展默认拒绝这些桌面扩展。内部去重 ID 为 `dom-` 加频道/角色/时间/正文/主图/可用原站消息编号的 SHA-256；`source_id` 来自原站并用于内部去重；按用户最新要求，消息和引用框均不展示编号。引用补齐或消失时更新原消息，不增加重复回答。

只在固定官方 origin/path/teacherId 的公开主列表读取。优先使用官方 Vue 实例 `chatRoomMsgList`，在正文前验证所选视图的 `userType`（teacher 仅 3，all 仅 1/3/4）、房间、撤回/私信回复/业务及当前权限。公开文本附带 `quoteContent` 时，只读取该消息已附带的公开引用及其昵称/编号，并独立排除撤回、私信、异房间、非公开业务和锁定记录；不查询账号资料、不递归嵌套引用。DOM 退路依赖原站公开角色标记（老师 vpg/t-no 且无 bg-blue，学员 user/me，助教 bg-blue），排除 VIP/隐藏节点；原站 DOM 不提供消息编号时省略。图片只允许三个明确公开域的 HTTPS 无凭证地址。每批最多 100 条、单条正文 10,000 字符/1 图，连同提问总批次控制在 220 KiB 以内。

宿主固定 `setViewMode('teacher'|'all')` 命令对应官方 `changeLookType(2|1)`，只调用已有公开筛选方法。切换等待当前官方读取完成，保存旧视图的历史最早游标、补载状态、时间水位和导出集合；返回同一会话缓存视图恢复这些状态，只校准最新页，不再次自动补载 20 页。首次视图仍进行有界历史读取，有实际水位缺口时补查。延迟回调不会被标成另一视图；每批及进度携带 view_mode，宿主分别保留两个 5000 条内存 feed、阅读位置及原生列表控件。缓存的源状态不写磁盘，账号/来源失效时丢弃。重连时重新注入当前选定视图。

登录退出、离线、离开聊天路由、进入历史页时输出 `source_ready:false,messages:[]`。新页面或恢复连接建立快照，不触发声音、桌面通知或历史回放提醒。心跳间隔约 5 秒；采样约 1 秒。完整导航的 `pagehide` 消息不保证送达，因此宿主在 `NavigationStarting`、WebView 销毁或切换目标时应立即标记来源未就绪。

这里的“历史页”指原扩展另建的云端副本标记 `data-yuen-history`，不包含官方公开列表原生向上补载的记录。现在允许通过官方 `changeLookType(2)` 筛选、`getChatRecord(roomId, cursorId)` 补载公开历史，不重写 HTTP 请求、不读取登录凭证。每次最多补 20 页，检查游标和时间确实前移；超时、停滞、身份变化、耗尽或容量上限停止。身份字符串仅在官方页面内部用于重校验，不对外输出。历史批次始终按 snapshot 提交，宿主继续复用老师消息严格验证与去重。

有界状态消息的必需字段固定为 `kind:'history_status'`、`state`、`pages`、`loaded`、`teachers`、`has_more`、`oldest`，可选字段仅允许 `audit`、`latest_state`、`latest_sync`、`view_mode`。`audit` 是所选视图投影和过滤原因计数及首末时间，不包含正文、昵称、消息编号或凭证；宿主严格验证字段、状态、时间和计数边界。状态不能代替消息验收或作为来源新鲜度的唯一证明。宿主只向自身目标顶层页面发送固定历史及视图命令。历史补载或最新校准期间原生采样为 1 秒，空闲时恢复 5 秒。桌面每视图内存上限 5000 条，普通 HTTP feed 仍默认 500 条。

约每 15 秒通过官方既有 `getChatRecord(roomId)` 主动读取最新页，不与历史请求或已有加载重叠。通过观察列表替换确认异步返回；新页与旧时间水位不重叠时，以最近页游标补查缺口至水位重叠。历史归档最早游标独立保留，并在下一次历史请求前从异步更新后的列表刷新，避免重复请求上一页。刷新最新页只替换官方源当前列表，宿主继续按哈希合并既有历史。超时或错误反映在 `latest_state`，不伪报成功校准。

## 宿主必须承担的边界

- 注入前验证当前顶层 WebView URI；收到消息时同时验证事件 `Source` 的官方文档 origin/path 和当前顶层 URI 的完整目标路由/teacherId。拒绝子框架、外站、登录页和本地页面发来的正文；不能只信 payload 的 `source` 或 role。
- 对 JSON 字符串、字段集合、模式、固定 scope、角色、时间、批次数、正文与图片 URL 再做严格校验，然后调用 feed。只渲染纯文本，不把消息作为 HTML、脚本、文件路径或动作执行。
- WebMessage 的 `Source` 证明发送文档的地址，不证明网站脚本和 DOM 诚实。目标页面本身也能调用 `window.chrome.webview.postMessage`；同一官方文档内的伪造无法靠另加一个网页可见的 secret 消除。不要向页面暴露任何具有文件、登录、交易或宿主执行能力的通用接口。消息接口仅接收这个有界的只读合同。
- 不注入浏览器 cookie、token 或网站存储；WebView2 的自身登录会话留在它的专用 profile 中。

## 源码依据与验证

严格角色组合已核对 2026-10-20 原站公开渲染模板 `chunk-0e46e8e9.19e48c6b.js?ver=20261013`：`.vpg/.t-no` 对应 userType 3/4；只有 4 取得 `.bg-blue`；`setcolor` 仅表示消息类型。SHA-256 为 `56bc9d8dc60bfebdbb04c779150030d5a0ebad7316e6d2a07a8329be56e6d436`。未读取任何账户资料。

`node desktop-source-smoke.cjs` 为合成 DOM/WebView2 的离线烟测，验证老师过滤、回答附带引用提问、学员独立发言/助教/VIP 隔离、没有扩展/网络/凭证调用、退出/离线/离开路由/历史页的未就绪状态、重连静默快照和重复注入。它不会启动窗口、登录或抓取真实消息。生产页面改版仍需重新验证 DOM 标记，离线通过不代表已接通真实账户。

`node desktop-history-smoke.cjs` 验证虚拟化公开列表、异步历史返回、107 条老师记录的分批投影、2 个前移游标、耗尽停止及幂等去重；还验证主动校准补回漏推送、超过 50 条的最近缺口恢复和既有历史保留（188 条唯一消息）。学员/助教/私信/异房间/撤回/付费正文和凭证字段设置为抛错读取器，确保过滤在正文读取前生效。当前官方源的实际读取范围及同花顺切换验收见 `../DESKTOP.md`。

引用扩展另覆盖对象/序列化引用、文字/图片/长度限制、引用延迟补齐且回答 ID 不变，以及引用自己的锁定/私信/异房间/撤回筛选；引用被拒绝时保留老师回答。`smoke_desktop.py` 验证宿主字段合同、更新/移除引用时不重复、无效整批原子拒绝和 HTTP 默认拒绝引用扩展。

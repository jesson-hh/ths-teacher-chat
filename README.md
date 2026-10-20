# 同花顺老师消息看板

一个独立的 Windows 聊天阅读组件：在程序内登录约牛，将有权查看的公开聊天室嵌入同花顺的自定义看板。采用黑灰红配色，不需要 Chrome 或浏览器扩展。

## 功能

- **只看老师 / 全部聊天**：前者保留老师回答及引用提问，后者显示公开老师、学员、助教发言。
- 回答下面展示对应提问、公开昵称、时间及图片；界面不显示消息编号。
- 历史分页、加载更早、返回最新、后台最新页校准和最近消息缺口补查。
- 两个视图分别保留消息和阅读位置，返回已加载视图直接使用缓存，后台只校准最新消息；首次访问才自动补载历史。
- 同花顺切换其他看板时自动隐藏，返回“老师消息”时恢复。

当前默认频道为“知行合一 · 交易逻辑专栏”。当前版本没有任意频道选择器。

## 安装前准备

已验证的环境：Windows 11 64 位、同花顺 9.60.80、Python 3.13、pywebview 6.2.1、pythonnet 3.1.0、PyInstaller 6.20.0。

1. 安装同花顺并确认可以正常启动。
2. 安装 Python 3.13 64 位，安装时启用 Python Launcher 或 PATH。
3. 安装 Microsoft Edge WebView2 Runtime。系统已有时无须重复安装：[微软官方下载](https://developer.microsoft.com/microsoft-edge/webview2/)。
4. 准备可正常登录该约牛频道的账号；扫码、密码及验证码由用户在官方登录页完成。

这里采用**源码构建安装**。普通使用时不需要启动任何 Python 服务，也不依赖旧的 `127.0.0.1:18765` 网页预览。

## 第一步：下载与构建

在能访问此私有仓库的电脑上，用 GitHub CLI 克隆，或在 GitHub 点击 Code → Download ZIP 并解压：

```powershell
gh repo clone jesson-hh/ths-teacher-chat
cd ths-teacher-chat
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_desktop.ps1 -Python .\.venv\Scripts\python.exe
```

构建产物位于：

```text
%LOCALAPPDATA%\THSTeacherDesktop\app\THSTeacher\THSTeacher.exe
```

如需把构建好的程序移到另一台 Windows 电脑，复制整个 `THSTeacher` 文件夹，包含 `_internal`；不能只复制 EXE。目标电脑仍需 WebView2 Runtime，并需要执行下面的看板配置。

## 第二步：在同花顺创建空白看板

1. 启动同花顺，进入自定义页面/看板的新建入口。
2. 新建一个专供此组件使用的空白页面，保存为 **老师消息**。
3. 保持这个页面打开。不要选择已有行情或交易模块作为承载区域。

菜单名称可能随同花顺版本变化。下面的画布参数已在 9.60.80 验证；其他版本需要核对画布类名及控件 ID。

## 第三步：创建配置和快捷方式

在仓库目录运行，将 `--ths-exe` 改成自己电脑实际的 `hexin.exe` 路径：

```powershell
.\.venv\Scripts\python.exe .\install_desktop.py `
  --board-name "老师消息" `
  --ths-exe "D:\app\同花顺\同花顺\hexin.exe" `
  --exe-path "$env:LOCALAPPDATA\THSTeacherDesktop\app\THSTeacher\THSTeacher.exe" `
  --canvas-class "AfxFrameOrView140s" `
  --control-id 59648 `
  --top-inset 28
```

可以先在命令末尾加 `--dry-run` 核对配置，再去掉此参数安装。

安装只在当前用户目录写 `board_config.json`，并创建桌面/开始菜单中的“同花顺老师消息”快捷方式；不会修改同花顺安装文件。`59648` 是画布控件 ID，不是临时 HWND。`top-inset` 单位为逻辑像素，本机 150% 缩放下 28 对应 42 个物理像素。

## 第四步：启动和登录

1. 双击桌面 **同花顺老师消息**，再打开同花顺的 **老师消息** 看板。
2. 首次使用点击组件右上角 **设置 → 约牛登录**，在官方页面完成登录。
3. 接通后自动返回消息列表。顶部选择 **只看老师** 或 **全部聊天**。
4. 使用 **上一页 / 下一页** 浏览已加载内容；**加载更早** 继续读取官方历史；**最新** 返回最新页。

组件是独立进程，需要与同花顺同时运行；打开同花顺本身不会自动启动组件。避免同时启动多份组件。

## 缓存与历史

- 每个视图最多保留本次运行最近 **5000 条**消息，每页 **60 条**。
- 首次进入一个视图自动补载最多 20 页官方历史，更多内容通过“加载更早”继续。
- 返回已访问的视图复用消息、阅读位置及原生列表，不重复启动一轮历史补载；后台仍读取最新页校准，有实际缺口时补查。
- 缓存仅在**本次运行的内存**中，程序退出后不会保留消息正文；下次启动重新读取。
- 退出账号、离开指定来源或来源失效时清空显示；不保证取得官方当前账号未提供的更早历史。

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 老师消息看板空白 | 确认组件正在运行，再检查看板名称和 `hexin.exe` 路径是否与配置一致。 |
| 提示未登录或会话过期 | 设置 → 约牛登录，由用户重新登录。 |
| 消息停止更新 | 设置 → 重新连接；同时确认网络及账号在官方页面可读。 |
| 只有较新的历史 | 点击加载更早；每轮最多 20 页，且受原站可读历史范围限制。 |
| 顶部留白或覆盖标签栏 | 核对同花顺版本和 `top-inset`，不要把控件 ID 当作窗口句柄。 |
| 双击 EXE 无窗口 | 有看板配置时组件可能在等待“老师消息”页面；打开该看板查看。 |
| 看到诊断显示在线但看板为空 | 诊断可能是退出前的旧文件，需同时核对其中 PID 是否仍存活。 |

运行配置与诊断在 `%LOCALAPPDATA%\THSTeacherDesktop\`；登录会话在其中的 `webview-profile`。**不要把这些用户目录提交到 GitHub。**

## 更新与退出

更新源码后，先在组件中选择“设置 → 退出组件”，再重新运行构建命令，最后通过原快捷方式启动。既有看板配置和程序自身的登录目录会保留。

卸载时先退出组件，再删除其快捷方式和 `%LOCALAPPDATA%\THSTeacherDesktop`。删除该目录会一并移除组件自己的登录会话。

## 开发验证

```powershell
.\.venv\Scripts\python.exe .\smoke_desktop.py
.\.venv\Scripts\python.exe .\smoke_server.py
node .\desktop\desktop-source-smoke.cjs
node .\desktop\desktop-history-smoke.cjs
```

JavaScript 离线检查需要 Node.js。原生消息链路不需要 Node.js。

主要入口：`desktop_app.py`；消息界面：`native_board_app.py`；看板绑定：`desktop_board.py`；官方登录/消息来源：`desktop_webview.py`、`desktop/desktop-source.js`。

## 实现范围

这是把组件自己的 Windows 子窗口嵌入指定空白看板的方案，并非同花顺官方插件。登录和接收来源使用组件自己的 WebView2；阅读区为原生 WinForms。只处理当前账号可见的公开频道内容，不读取私信、不提取登录凭证、不操作交易控件。

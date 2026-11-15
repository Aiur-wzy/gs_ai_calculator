# 安装与获取数据库

## 安装正式版

首个正式发行是 App **0.46.0-r19**，配套数据库为 **db-7.2.50-r6**。支持 Windows 10/11 x64。请以[应用发行页](https://github.com/Aiur-wzy/gs_ai_calculator/releases)和[数据库发行页](https://github.com/Aiur-wzy/gs_ai_calculator_db/releases)实际提供的附件和 SHA-256 校验值为准。仓库源码 ZIP 不包含 EXE 或完整数据库。

1. 下载并解压小安装器 ZIP，运行 `GenshinCalcSetup.exe`。
2. 选择「安装程序和数据库（推荐）」即可安装完整组合。选择「只安装程序，稍后下载数据库」可先装应用，随后在 GUI 中选择「下载数据库」。首次安装受签名索引约束，并会验证下载文件。
3. 程序安装在 `%LOCALAPPDATA%\GenshinCalcStable\app`，默认用户资料目录为 `%LOCALAPPDATA%\GenshinCalcStable\data`。正式版从新的资料目录开始，不继承旧开发版会话和设置。自己保留的独立方案可按需在新版打开。

小安装器和 App EXE 未做 Windows 代码签名。Windows 可能显示发布者或安全提示，请根据发行页核对下载来源和 SHA-256 校验值。安装时，项目的更新索引仍须通过签名验证。

## 使用独立程序 ZIP

解压整个应用目录并保留其中的资源和依赖。`GenshinCalc.exe` 是 CLI 入口，`GenshinCalcGUI.exe` 是图形界面入口。独立程序 ZIP 未内置数据库。初次打开 GUI 时，可按界面提示下载配套数据库，或选择本地数据库包及对应的 `database-install-pair.json` 补装。

当前推荐由 GPT-6.1 Sol 或更高能力模型通过 CLI 完成排轴和计算，再用 GUI 打开方案、人工看图审核。应用内原生 Agent 与 DeepSeek 在复杂任务中仍不稳定。参见 [CLI 工作流](cli-workflow.md)。

在程序目录双击 `GenshinCalcGUI.exe`，然后从文件菜单选择原生方案。也可以在程序目录打开终端并执行以下命令。加载方案不会自动重新计算。

```powershell
.\GenshinCalcGUI.exe gui --open '.\my-task\native.plan.json'
```

图形界面和 CLI 使用同一套领域服务。查看图形时还应核对当前文件、数据库版本、计算状态和近似说明。只有聊天文字或一个能打开的草稿，不能证明完整计算已经通过。

## 离线安装和后续更新

- 要完整离线安装，先下载对应版本的签名索引 `latest.json`、应用 ZIP 和数据库包。在 Setup 中点击「使用离线发行包」，依次选择这三个文件。
- 在线更新会列出通过验签且与已装版本兼容的组合。可选范围随发行变化，可能包含仅更新程序、仅更新数据库或同时更新。
- 在终端执行 `GenshinCalcSetup.exe --uninstall` 可卸载通过 Setup 安装的程序。此操作保留数据库、方案、会话和设置。

普通 CLI 查询和计算无需在 App 内配置 DeepSeek Key。数据库可能收录未上线、测试、废弃和状态未知的条目，采用未上线或未知实体前需要确认。详见[数据声明](data-notice.md)。

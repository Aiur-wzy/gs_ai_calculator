# 第三方软件与数据

项目的 MIT 许可适用于项目自有代码。第三方软件与游戏资料保留各自的声明和适用条款。

- 应用的对话运行时复用 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 和 Cordis 组件。实际版本按 `agent-runtime/pnpm-lock.yaml` 与 `frontend/pnpm-lock.yaml` 固定。
- Windows GUI 使用 [pywebview](https://github.com/r0x0r/pywebview)。构建工具和其余 Python 依赖见 `requirements.txt`。
- 发布包的精确组件、许可证文件和未声明许可项见随包生成的 `sbom.cdx.json`、`license-manifest.json` 与 `THIRD_PARTY_NOTICES.txt`。
- 基础游戏数据整理自 [YuanShenResources](https://gitlab.com/GuraFoundation/YuanShenResources)。数据库发行文件分别记录固定提交、上游版本、来源与项目补充知识。详见[数据来源与声明](docs/data-notice.md)。

2026-11-15 已完成 r18 原有23项软件依赖材料核查。原文、固定版本来源、声明差异和逐项结论见 [third_party/reviewed-licenses](third_party/reviewed-licenses/README.md)。重新生成的569组件清单剩余待复核数为0；这表示本次列出的材料事项已处理，不代表数据库取得新的许可，也不是对一切法律问题的保证。

发布包的 `license-review.json` 保留核查依据，`THIRD_PARTY_NOTICES.txt` 包含完整许可及必要声明。原元数据和核查结论分开记录；缺少作者专属全文的包明确标为“发布者声明＋标准条款”，不编造版权信息。LibreOffice、内嵌 DevTools 和 ONNX Runtime 的单独条件仍适用。历史 r18 包需连同后补的第三方材料目录分发；新构建自动将这些材料纳入包内。

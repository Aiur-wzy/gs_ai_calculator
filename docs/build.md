# 源码构建

本版源码对应 App **1.0.4**（`app-1.0.4-r2`） 与数据库 **db-7.2.54-r1**。Git 源码不带 SQLite、Node 可执行文件或已安装依赖。完整构建需取得配套数据库、数据库发行清单和受验的前代配对清单。

## 先核对源码

全新克隆或解压后，在安装依赖及生成文件之前执行：

```powershell
python scripts/export_public_source.py verify --target .
```

它检查 `SOURCE-SNAPSHOT.json` 中的路径、字节长度、摘要和已审测试夹具。根目录的 Git 元数据不属于源码清单；额外源码、生成物或文件改动仍会导致校验失败。公开仓的 `.gitattributes` 保留原文件字节，避免换行转换改变受验资源。修改后的开发目录无需再冒充原始快照。

## Python CLI

使用 Python 3.12或以上版本。在项目根目录建立环境并安装依赖：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m genshin_app --help
.\.venv\Scripts\python.exe -m genshin_app capabilities --command "equipment calc"
```

下文的 `python` 均指这个虚拟环境中的解释器；未激活环境时，请写成 `.\.venv\Scripts\python.exe`。

仅查看命令帮助和能力无需运行模型或启动 GUI。实际查询和计算需要能通过相应运行环境的数据库及程序身份核验。普通用户应优先使用已安装的正式 EXE 计算，生成原生方案后再到 GUI 审核。[CLI 工作流](cli-workflow.md)

Python 回归入口为 `python -m pytest`。开发期先运行受影响测试；完整回归需要安装下述锁定运行时依赖，部分发行验收还需要配套数据库和前代工件。缺少它们的源码环境不代表可以直接重现完整发行验收。

## GUI 与 DSH 运行资源

本版锁定 Node **24.19.0**、pnpm **11.25.0**、DSH **0.2.2-rc.3**。使用对应的 Windows Node，将 `node.exe` 放入 `agent-runtime/node.exe`，保留该发行的许可证材料。Node 字节摘要与运行时资源由源码中的发行身份核对。

本仓 [third_party/reviewed-licenses](../third_party/reviewed-licenses/README.md) 收录本版许可核查材料。构建默认读取其中的 `manifest.json`，校验准确依赖元数据、Node 字节及许可原文后生成随包说明，全程离线。更新依赖时必须复核并更新证据。`--license-evidence` 可指定另一份经过核对的材料，`--node-license` 如另行提供，必须与已核验的同版原文一致。

在项目根目录执行：

```powershell
pnpm --dir agent-runtime install --frozen-lockfile --prod --ignore-scripts --config.node-linker=hoisted --config.package-import-method=copy
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build:dsh-domain
pnpm --dir frontend generate:dsh-client-manifest
pnpm --dir frontend build
pnpm --dir frontend build:production
node scripts/generate_dsh_client_shell_manifest.mjs agent-runtime frontend agent-runtime/node.exe build/runtime-candidate.json
```

这些命令生成前端与运行时候选。核对候选和本版受验清单，不要仅为了绕过启动检查而改写大小、摘要或版本。若确实更新代码或依赖，应完成对应验证，再更新成套资源及发行身份。

开发环境的 GUI 入口是 `python app_entry.py --data-dir .\data gui`。该命令需要通过源码环境实际配置的启动核验。正式安装、补装和更新请使用已打包发行版。[安装指南](install.md)

## Windows EXE 构建输入

本版完整构建需准备以下文件，均放在工作区内，不能位于将被清理的 `build-py` 或目标 `dist` 下：

| 文件 | 用途 |
|---|---|
| `data/genshin.db` | 7.2.54 完整数据库，把发行页的数据库包解压后保存为 `genshin.db` |
| `release-inputs/1.0.4-r2/database-release-manifest.json` | 与该 7.2.54 字节对应的正式发行清单 |
| `agent-runtime/node.exe` | 与锁定发行匹配的 Windows Node |
| `release-inputs/accepted-1.0.4/release-pair-manifest.json` | 前一验收候选的同库配对清单，已随源码提供 |
| `release-inputs/accepted-1.0.3/stable-latest.json` | 构建时用于核对旧用户真实升级起点的签名索引 |

7.2.54 发行清单和数据库包由[数据发行页](https://github.com/Aiur-wzy/gs_ai_calculator_db/releases/tag/db-7.2.54-r1)提供。前代配对清单使用 [release-inputs](../release-inputs/README.md) 中指定路径，保留原始字节。

源码已固定正式发行身份及精确配对规则。准备齐全后，先读取 `python scripts/build_dist.py --help`，再使用：

```powershell
$buildTime = [DateTimeOffset]::UtcNow.ToString('o')
python scripts/build_dist.py --dist-root dist/local-1.0.4-r2 --database data/genshin.db --accepted-database-manifest release-inputs/1.0.4-r2/database-release-manifest.json --accepted-predecessor-pair-manifest release-inputs/accepted-1.0.4/release-pair-manifest.json --accepted-legacy-update-index release-inputs/accepted-1.0.3/stable-latest.json --native-node agent-runtime/node.exe --released-at $buildTime --no-app-execution
```

`--no-app-execution` 不启动生成的 App，但仍执行 Node 和打包资源校验。`--native-plan-only` 只生成构建计划，仍需配套数据库与锁定运行时输入。实际编译结束后须核对包内版本和文件清单。

Windows 小安装器和更新器构建时会逐字节核对封装内的数据库校验资源。小安装器的 `--verify-frozen-install` 验收入口会在临时目录安装已签名的本地 App 和数据库包，并核对安装后的文件和配对记录。归档检查与实际安装分别记录。

官方版本使用正式更新入口及生产公钥。安装器须能读取签名索引和匹配工件才可在线安装。上述命令用于本地构建，发行时间和工件摘要以正式发布记录为准。自行构建并对外分发时，应使用独立的发行身份及对应的验签配对。官方签名索引只验证官方工件的原始字节。

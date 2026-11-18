# 发行构建输入

App **1.0.2** 的构建前代清单为 `1.0.1/release-pair-manifest.json`，SHA-256 `0b99891fa54331a91012779bbbb01224e39524f76d70e8fe2f8d30c5d01d8b66`。数据库继续使用原 db-7.2.50-r6。


App **1.0.1** 的完整构建使用 `1.0.0/release-pair-manifest.json`。它来自已验收的 1.0.0 便携构建，SHA-256 为 `ac57fe3185d8f4be52f46be209003a4be310ec5355a2bec093d99cc3c48e4226`。安装版的在线更新使用已签名的公开索引。

App **1.0.0** 的完整构建使用 `r21/release-pair-manifest.json`。这是先于 1.0.0 构建并验收的本地预览版本的配对清单，SHA-256 为 `ac1a1f7aa001933e179e942ee8eaac48f0cf7f545ca8b86fe3d949cce334d5a9`。此处只提供构建所用的元数据。公开更新从正式发布的 r20 直接升级到 1.0.0，用户无需取得本地预览版本。

其余 1.0.0 构建输入见[构建指南](../docs/build.md)。

## 历史资料

本目录另保留旧开发版 r18 构建曾用的前代清单原始字节。

- `r17-release-pair-manifest.json` 固定当时的前代 App r17 与 DB r4。
- `r4/database-release-manifest.json` 对应前代 r4 数据库。完整升级验证还需要同一版本的 `genshin.db`，需另行取得。
- 历史 r5 数据库清单可在[数据仓](https://github.com/Aiur-wzy/gs_ai_calculator_db)找到。

# Hermes Agent 执行任务：家庭照片智能相册 V1

你需要为家庭照片库部署一个完全本地化的智能照片索引系统。

## 架构

- QNAP NAS：只存储原始照片。
- GPU 台式机：运行 Immich Server、PostgreSQL、Immich Machine Learning。
- Hermes Agent：通过 Immich API 做搜索、统计和创建逻辑相册。

完整设计见同目录 `README.md`。

## 绝对安全约束

1. NAS 原始照片必须只读挂载。
2. 不得移动、删除、重命名或修改任何 NAS 原始文件。
3. 不得为了方便去掉 Docker volume 的 `:ro`。
4. PostgreSQL 不得安装在 NAS SMB/NFS 网络目录。
5. 所有数据库、缓存、Docker 持久化数据放在台式机本地 SSD。
6. 不得自动清空 Immich Trash。
7. 不得给 Hermes 实现资产删除 API。
8. 密钥、NAS 密码、Immich API Key 不得输出到日志或提交 Git。
9. 遇到未知 GPU 型号、未知路径或危险权限时，停止相关写操作并输出诊断，不要猜。
10. 优先使用 Immich 当前官方 release 的 compose、env、OpenAPI 和硬件加速配置，不依赖旧教程。

## 执行阶段

### Phase 0：环境探测

- 检查操作系统。
- 检查 Docker / Docker Compose。
- 检查 GPU 厂商、型号、显存和驱动。
- 检查 NAS 网络连通性。
- 检查照片共享路径。
- 输出环境报告。

### Phase 1：NAS 只读挂载

- 使用 `immich_reader` 专用只读账号。
- 将 NAS 照片挂载到 `/mnt/nas/photos`。
- 做写入失败测试。
- 只有写入确实失败才继续。

### Phase 2：Immich

- 在本地 SSD 建 `/opt/immich`。
- 下载 Immich 当前官方 release 的 `docker-compose.yml` 和 `example.env`。
- 数据库放 `/opt/immich/data/postgres`。
- Immich data 放 `/opt/immich/data/library`。
- 把 `/mnt/nas/photos` 映射到 `/mnt/external/photos:ro`。
- 启动 Immich 并做 healthcheck。

### Phase 3：GPU

- NVIDIA 使用 CUDA。
- AMD 使用 ROCm。
- Intel 使用 OpenVINO。
- 以 Immich 当前官方硬件加速配置为准。
- 验证推理期间 GPU 实际有利用率/显存占用。
- 不允许仅因为容器启动成功就判定 GPU 加速成功。

### Phase 4：External Library

- 创建 External Library，路径 `/mnt/external/photos`。
- 执行首次扫描。
- 先跑 metadata 和 thumbnail。
- 验收后再跑 OCR、Smart Search、Face Detection、Facial Recognition、Duplicate Detection。
- 不要一开始把所有 Job Concurrency 拉满。

### Phase 5：中文搜索

- 测试中文 Smart Search。
- 测试中文 OCR。
- 如果默认模型效果明显不足，比较 Immich 当前官方支持的多语言模型。
- 8GB 显存优先中等规模模型。
- 先用小批样本验证，再决定是否全量重跑 Smart Search。

### Phase 6：Hermes Skill

建立：

```text
skills/immich-photo/
```

实现：

```text
healthcheck
search_text
search_people
search_ocr
search_time_location
create_album
add_assets_to_album
report_library
```

禁止：

```text
delete_asset
empty_trash
move_original
rename_original
```

Immich API endpoint 必须从当前服务器 OpenAPI 核对，不允许凭旧版本记忆硬编码。

### Phase 7：验收

依次验证：

- 海边语义搜索。
- 飞机语义搜索。
- 中文 OCR。
- 单人物。
- 双人物。
- 城市。
- 时间。
- 人物 + 城市 + 时间。
- 创建 `TEST - Hermes Photo Album`。
- 加入 5 张照片。
- NAS 原始文件数量、路径、mtime 未发生变化。

### Phase 8：备份

- 对 PostgreSQL 建每日备份。
- 保存 compose、env 模板和部署记录。
- Secret 不进入备份日志正文。
- 实现升级前自动备份检查。

## 执行日志

全过程维护：

```text
docs/immich-photo-library/deployment-status.md
```

每完成一个阶段记录：

- 完成时间。
- 执行命令。
- 关键结果。
- 异常。
- 是否需要人工处理。
- 下一步。

## GitHub 协作方式

如果发现以下情况，不要自行冒险绕过：

- NAS 实际可写。
- GPU 后端无法确认。
- Immich 当前版本与本方案示例不一致。
- OpenAPI endpoint 已变化。
- 需要删除、移动或修改原始照片。
- 需要扩大 Hermes 权限。
- 发现可能导致数据损失的步骤。

请直接在本方案对应的 GitHub PR / Issue 中说明：

1. 当前 Phase。
2. 实际环境。
3. 执行的命令或配置。
4. 实际错误/输出。
5. 你建议的解决方案。
6. 是否涉及原始照片写权限或破坏性操作。

不要在评论中粘贴 NAS 密码、API Key、Token 或其他 Secret。

## 最终输出

完成后提交：

1. 环境报告。
2. 部署拓扑。
3. 实际目录。
4. 实际 Compose 修改。
5. GPU 验证结果。
6. Immich URL。
7. External Library 状态。
8. 索引资产数。
9. AI Job 状态。
10. Hermes Skill 文件列表。
11. 10 项验收结果。
12. 备份策略。
13. 尚未完成的问题。

**不要执行任何可能破坏原始照片的操作。**

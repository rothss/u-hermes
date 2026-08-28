# 家庭照片智能相册第一版落地实施手册

> QNAP NAS + GPU 台式机 + Immich + Hermes Agent
>
> 版本：V1.0 · 2026-08-28

目标：在**不移动、不删除、不修改 NAS 原始照片**的前提下，用带 GPU 的台式机完成照片索引、人物识别、OCR、地点/时间检索和智能搜索，并让 Hermes Agent 通过 Immich API 做检索、建相册和自动化。

## 1. 最终架构

```text
                         ┌──────────────────────┐
                         │     Hermes Agent     │
                         │ 搜索 / 建相册 / 批处理 │
                         └──────────┬───────────┘
                                    │ API
                                    ▼
┌──────────────────┐      ┌──────────────────────────┐
│     QNAP NAS      │ SMB  │      GPU 台式机          │
│                  ├─────►│                          │
│ /Photos 原始图库  │ RO   │ Immich Server            │
│ 只读，不移动不删除 │      │ PostgreSQL               │
└──────────────────┘      │ Immich Machine Learning  │
                          │ OCR / CLIP / Face         │
                          └──────────────────────────┘
```

### 第一版原则

1. NAS 只负责存储原始照片。
2. Immich 数据库和缓存放台式机本地 SSD，不放 SMB/NFS。
3. NAS 原始照片以只读方式挂载。
4. Immich 只做智能索引，不负责移动、重命名、删除原图。
5. Hermes 第一阶段只获得查询、统计、创建相册、加入相册权限。
6. Hermes 不得删除 Immich Asset、清空 Trash、修改 NAS 文件或去掉只读挂载。
7. 先完成全量索引和验收，再做增量自动化。

---

## 2. 推荐部署方式

第一版推荐 Immich Server、PostgreSQL、Machine Learning 全部运行在 GPU 台式机；QNAP J3355 只提供 SMB 原图。

这样避免：

- NAS CPU 成为 AI 瓶颈；
- PostgreSQL 走网络共享；
- 跨机器数据库故障；
- Hermes 直接遍历几十万张照片做视觉推理。

台式机建议至少 16 GB RAM，并给数据库、缩略图和缓存预留本地 SSD 空间。

---

## 3. Phase 0：环境探测

任何写操作之前，Hermes 先生成 `deployment/site.env`：

```bash
# NAS
NAS_HOST=
NAS_SHARE=
NAS_PHOTO_SUBDIR=
NAS_USERNAME=

# HOST
IMMICH_HOST_IP=
IMMICH_BASE_DIR=/opt/immich
IMMICH_PORT=2283

# GPU
GPU_VENDOR=
GPU_MODEL=
GPU_VRAM_GB=
GPU_BACKEND=

TZ=Asia/Singapore
```

### GPU 探测

Linux / WSL：

```bash
nvidia-smi || true
rocminfo || true
lspci | grep -Ei 'vga|3d|display' || true
```

Windows：

```powershell
Get-CimInstance Win32_VideoController |
Select-Object Name, AdapterRAM, DriverVersion
```

判断：

```text
NVIDIA -> cuda
AMD    -> rocm
Intel  -> openvino
```

如果无法确认 GPU 后端，不要猜；先 CPU 模式部署并记录诊断。

---

## 4. Phase 1：NAS 只读挂载

在 QNAP 建专用账号：

```text
immich_reader
```

只给照片共享目录 Read Only 权限，禁止删除、写入、改名和创建目录。

Linux / WSL 推荐：

```bash
sudo apt update
sudo apt install -y cifs-utils
sudo mkdir -p /mnt/nas/photos
sudo mkdir -p /etc/immich
sudo nano /etc/immich/nas-credentials
```

凭据：

```text
username=immich_reader
password=REPLACE_ME
```

```bash
sudo chmod 600 /etc/immich/nas-credentials

sudo mount -t cifs \
  //NAS_IP/SHARE_NAME \
  /mnt/nas/photos \
  -o credentials=/etc/immich/nas-credentials,ro,vers=3.0,iocharset=utf8
```

### 强制安全验收

```bash
touch /mnt/nas/photos/__immich_write_test__
```

正确结果必须是：

```text
Permission denied
或
Read-only file system
```

如果写入成功，**立即停止部署**。

开机挂载示例：

```fstab
//NAS_IP/SHARE_NAME /mnt/nas/photos cifs credentials=/etc/immich/nas-credentials,ro,vers=3.0,iocharset=utf8,_netdev,nofail,x-systemd.automount 0 0
```

---

## 5. Phase 2：Immich

```bash
sudo mkdir -p /opt/immich
sudo chown -R "$USER":"$USER" /opt/immich
cd /opt/immich

wget -O docker-compose.yml \
  https://github.com/immich-app/immich/releases/latest/download/docker-compose.yml

wget -O .env \
  https://github.com/immich-app/immich/releases/latest/download/example.env

cp docker-compose.yml docker-compose.yml.original
cp .env .env.original
```

数据库必须放本地 SSD，例如：

```env
UPLOAD_LOCATION=/opt/immich/data/library
DB_DATA_LOCATION=/opt/immich/data/postgres
TZ=Asia/Singapore
DB_PASSWORD=REPLACE_WITH_RANDOM_VALUE
DB_USERNAME=postgres
DB_DATABASE_NAME=immich
```

创建目录：

```bash
mkdir -p /opt/immich/data/library
mkdir -p /opt/immich/data/postgres
```

### External Library volume

在 `immich-server` 的 volumes 增加：

```yaml
- /mnt/nas/photos:/mnt/external/photos:ro
```

`ro` 不允许删除。

启动前：

```bash
docker compose config
```

启动：

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=200
```

访问：

```text
http://GPU_PC_IP:2283
```

---

## 6. Phase 3：GPU Machine Learning

使用 Immich **当前 release** 对应的官方硬件加速配置，不复制旧博客里的 compose 片段。

后端：

```text
NVIDIA -> CUDA
AMD    -> ROCm
Intel  -> OpenVINO
```

NVIDIA 必须同时验证宿主机和 Docker：

```bash
nvidia-smi
```

以及一个 CUDA 容器内的 `nvidia-smi`。

Hermes 不允许仅凭 ML 容器“启动成功”就宣布 GPU 已启用，必须观察实际推理期间 GPU 使用率/显存占用。

Immich Server 和 Machine Learning 必须保持同版本。

---

## 7. Phase 4：External Library 与首次索引

在 Immich 管理界面创建 External Library：

```text
/mnt/external/photos
```

首次不要一次跑满全部任务。

### Phase A

```text
Library Scan
Metadata Extraction
Thumbnail Generation
```

验收：时间轴、EXIF、缩略图、原图读取均正常。

### Phase B

```text
OCR
Smart Search
```

测试：

```text
海边
飞机
截图
身份证
发票
会议室
酒店
航班
```

### Phase C

```text
Face Detection
Facial Recognition
Duplicate Detection
```

人工先命名高频家庭成员和常见同行人，再做组合检索。

---

## 8. 中文语义搜索

先用 Immich 当前默认模型对 500～2000 张样本测试，再决定是否切换多语言模型。

测试词建议：

```text
海边
飞机窗外
家庭聚餐
文档截图
雪山
酒店房间
会议
登机牌
身份证
发票
厦门旅游
沈阳街景
```

8 GB 显存优先中等规模模型。若发生 OOM，优先降低 Job Concurrency，而不是直接换更小模型。

---

## 9. OCR 与“截图/文档”分类

OCR 用于检索，不作为唯一分类依据。

第一版只创建逻辑相册，例如：

```text
AI分类 - 疑似截图
AI分类 - 疑似文档
AI分类 - 疑似发票
AI分类 - 疑似证件
AI分类 - 疑似登机牌
```

综合信号：

```text
OCR 文本密度
+ Smart Search 语义
+ EXIF 特征
+ 图片尺寸/比例
```

不移动 NAS 原文件。

---

## 10. Hermes Agent 的角色

Hermes 不直接遍历整个 NAS 做逐张视觉推理。

正确路径：

```text
自然语言
↓
拆解人物/时间/地点/OCR/语义条件
↓
调用 Immich API
↓
读取 Asset IDs 和 metadata
↓
二次过滤/聚类
↓
返回结果或创建逻辑相册
```

---

## 11. Immich API Key

创建专用 Key：

```text
hermes-photo-agent
```

保存到 Hermes 本地 Secret：

```env
IMMICH_BASE_URL=http://GPU_PC_IP:2283
IMMICH_API_KEY=REPLACE_ME
```

要求：

- 不进 Git；
- 不写入日志；
- 不放进 `SKILL.md`；
- 不在 Issue/PR 评论中输出。

---

## 12. Hermes Skill 目录

```text
skills/
└── immich-photo/
    ├── SKILL.md
    ├── README.md
    ├── scripts/
    │   ├── immich_client.py
    │   ├── healthcheck.py
    │   ├── search_assets.py
    │   ├── search_people.py
    │   ├── search_ocr.py
    │   ├── search_time_location.py
    │   ├── create_album.py
    │   ├── add_assets_to_album.py
    │   └── report_library.py
    └── tests/
        └── smoke_test.py
```

第一版实现：

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

明确禁止：

```text
delete_asset
empty_trash
move_original
rename_original
```

API endpoint 必须从当前 Immich OpenAPI 文档核对，不允许依赖旧版本记忆。

---

## 13. Skill 行为约束

1. 默认只读。
2. 允许创建 Immich 相册。
3. 允许把现有资产加入相册。
4. 不允许删除 Asset。
5. 不允许清空 Trash。
6. 不允许移动、重命名、修改 NAS 文件。
7. 不允许去掉挂载参数 `:ro`。
8. 批量操作超过 500 个 Asset 时，先输出数量和操作计划。
9. 批处理保存日志，但日志中不得出现 Secret。
10. 任何危险权限状态都必须 fail closed。

---

## 14. 自然语言例子

用户：

```text
找过去十年我和父亲一起旅游的照片，按城市分组。
```

Hermes 应：

```text
查人物 ID
→ people=[我,父亲]
→ date=过去十年
→ 查询 Immich
→ 按 location.city 聚类
→ 返回各城市数量和代表结果
```

只有用户明确要求“建相册”时才创建：

```text
家庭旅行 - 厦门
家庭旅行 - 沈阳
家庭旅行 - 北京
```

---

## 15. 全量索引限流

第一轮建议保守并发：

```text
Library Scan          低～中
Metadata Extraction   中
Thumbnail             中
OCR                    1～2
Smart Search           1～2
Face Detection         1～2
```

持续观察：

```bash
nvidia-smi
docker stats
```

关注 VRAM、RAM、NAS 网络、SSD IO、OOM、容器重启。

---

## 16. 增量扫描

第一版稳定后，使用 Immich External Library 自带调度每天凌晨扫描一次即可。

不要再由 Hermes 创建重复 cron，也不要分钟级遍历大图库。

---

## 17. 备份

至少备份：

```text
PostgreSQL
Immich 配置
compose/.env 模板
Hermes Skill 配置（不含 Secret）
```

数据库包含人物命名、人脸关联、相册和应用层元数据；NAS 原图不依赖 Immich 数据库存活。

升级流程必须：

```text
读取 release notes
→ 检查 breaking changes
→ 数据库备份
→ compose/env 备份
→ 升级 Server + ML
→ healthcheck
→ OCR/Smart Search/Face smoke test
```

禁止自动跟随 `latest` 无审查升级。

---

## 18. 永久安全边界

Hermes 永久禁止自动执行：

```bash
rm -rf /mnt/nas/photos
mv /mnt/nas/photos/...
find /mnt/nas/photos -delete
chmod -R ...
chown -R ...
```

也禁止：

```text
把 External Library 改为可写
批量删除 Asset
清空 Trash
```

允许：

```text
读取
扫描
生成缩略图
生成 embedding
OCR
人脸识别
搜索
统计
创建相册
把照片加入相册
输出报告
```

---

## 19. 验收清单

### NAS

- [ ] 使用专用只读账号
- [ ] SMB 挂载成功
- [ ] `touch` 写入测试失败
- [ ] 重启后仍保持只读挂载
- [ ] NAS 原目录结构未改变

### Immich

- [ ] Web UI 可访问
- [ ] PostgreSQL 位于本地 SSD
- [ ] External Library volume 带 `:ro`
- [ ] 扫描成功
- [ ] 时间轴、缩略图正常

### GPU

- [ ] 宿主机识别 GPU
- [ ] Docker 识别 GPU
- [ ] Machine Learning 正常
- [ ] 实际 Smart Search/Face job 使用 GPU
- [ ] 无持续 OOM

### AI

- [ ] 中文语义搜索可用
- [ ] OCR 搜索可用
- [ ] 人脸检测/聚类可用
- [ ] 人物命名可用
- [ ] 重复照片检测可用

### Hermes

- [ ] 独立 API Key
- [ ] Secret 不进 Git
- [ ] healthcheck 正常
- [ ] 语义/人物/OCR/时间地点搜索正常
- [ ] 创建相册正常
- [ ] 无删除能力

---

## 20. 第一轮验收用例

1. 搜索“海边”。
2. 搜索“飞机”。
3. 用一张已知截图里的中文做 OCR 搜索。
4. 搜索一个已命名人物。
5. 搜索两个人同时出现。
6. 年份 + 城市。
7. 人物 + 城市。
8. 人物 + 城市 + 时间。
9. 创建 `TEST - Hermes Photo Album` 并加入 5 张照片。
10. 确认 NAS 原文件路径、数量和 mtime 未被修改。

---

## 21. 第一版完成标准

达到下列状态后停止扩功能，先实际使用：

```text
NAS 原图只读
+ Immich 浏览正常
+ Smart Search 可用
+ OCR 可用
+ 人物聚类可用
+ GPU 加速确认生效
+ Hermes 能查询
+ Hermes 能建相册
+ 数据库有备份
```

第二版再考虑：自动旅行事件识别、自动城市相册、截图/文档自动分类、Qdrant、LLM Caption、家庭成员关系图谱和自然语言照片问答。

---

## 22. 核心原则

```text
QNAP 原始文件 = 真相来源
Immich          = 可重建的智能索引层
Hermes          = 索引层上的自动化代理
```

AI 索引可以重建，数据库可以恢复，相册可以重新生成；**NAS 原始照片不允许被自动化代理修改。**

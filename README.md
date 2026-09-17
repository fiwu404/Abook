# Abook — 教材到考试的闭环 MVP

基于 Vue 3、TypeScript、FastAPI、MySQL、Celery 和 Redis。第一版仅接收 PDF，只生成与审核单选题。流程按 `AGENTS.md` 实现：教材解析、人工校对、内容索引、知识点审核、试题校验和审核、组卷发布、收卷判分、错题重练。

## 启动

```bash
cp .env.example .env
docker compose up --build
```

打开 <http://localhost:5173>。首次用 `admin/admin12345678` 登录。admin 可完成上传、校对、审核、组卷、考试和错题练习全部流程，并在“账号管理”创建教师、学生账号。第一版教师只可阅读已索引教材；学生可阅读教材、参加考试和错题练习。系统不开放自助注册。部署前修改 `.env` 中的数据库密码、`ADMIN_PASSWORD` 和 `APP_SECRET`。

### 模型配置

首次启动会将环境变量中的默认模型写入数据库。默认连接**宿主机运行的 Ollama**：`MODEL_BASE_URL=http://host.docker.internal:11434/v1`，文本模型为 `qwen3-vl:8b`，扫描页 OCR 模型为 `qwen3-vl:4b`。Docker Compose 通过 `extra_hosts: host.docker.internal:host-gateway` 从普通桥接网络访问宿主机，未使用 host 网络模式。[Ollama 的 OpenAI 兼容接口](https://docs.ollama.com/api/openai-compatibility)支持此 `/v1/chat/completions` 地址；[Docker 的 host-gateway 映射](https://docs.docker.com/compose/how-tos/networking/)用于桥接容器访问宿主机。

admin 可在“模型接入”添加或编辑提供商，填写显示名称、API 根地址、接口类型和可选密钥。支持 Ollama 与提供 `/models`、`/chat/completions` 的 OpenAI 兼容云端接口。选择提供商后点击“获取支持的模型”，再分别选择文本模型和视觉 OCR 模型；“测试可用性”会向所选模型发送实际的文本或图片请求，测试通过后保存模型选择。后续也可直接向 `model_providers` 表添加提供商，刷新页面即可显示；云端密钥建议通过管理页面填写。密钥在数据库中加密，接口只返回是否已设置；更换 `APP_SECRET` 后需重新填写密钥。模型查询使用 Ollama 的模型列表接口或 [OpenAI 兼容的模型列表接口](https://platform.openai.com/docs/api-reference/models/list)。

**当前本机 Ollama 仅监听 `127.0.0.1:11434`，桥接容器尚不能访问。** 在 Linux 上需将 Ollama 的监听地址改为宿主机可达地址，例如用 `sudo systemctl edit ollama.service` 添加：

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

然后运行 `sudo systemctl daemon-reload && sudo systemctl restart ollama`。仅在 Docker 端配置 `host.docker.internal` 不能改变 Ollama 的回环监听；如用 `0.0.0.0`，应通过防火墙限制 11434 的外部访问。[Ollama 官方说明](https://docs.ollama.com/faq)列出了 `OLLAMA_HOST` 的 Linux 配置方法。也可设置为实际 Docker bridge 网关 IP，避免监听所有网卡。若原有 `.env` 中模型项为空，Compose 的默认值仍会生效；已有数据库模型配置不会被环境变量覆盖。

Ollama 暂不可达时，PDF 的文本页仍可解析，扫描页会标出待处理问题；admin 可人工修正文稿、补充知识点和录入题目。AI 任务会记录明确错误。质量问题不会被静默跳过。

## 工作顺序

1. admin 上传 PDF，启动解析任务，在“后台任务”查看进度。
2. 在“教材与学习”核对 PDF 页码、印刷页码、章节与 OCR 文本；保存局部修正后确认映射并建立内容索引。未处理的问题继续显示。
3. AI 提取知识点或人工补充；admin 逐条通过或退回。无原文出处的人工补充可入库，但不能出题。
4. 对已审核且有出处的知识点 AI 出题或人工录题。结构、答案、重复和原文依据自动校验；admin 确认题目。
5. admin 选择知识点、题量、每题分值和难度组卷，核对总分，设置考试时段并发布。发布时冻结题目、答案和评分规则。
6. admin 或学生在时段内答题，答案自动保存；提交或到时判分。成绩包含错题、解析、原文依据及知识点掌握情况。错题可用已审核原题重练；变式题生成后仍需 admin 审核通过。

## 任务与数据

Celery worker 以 `WORKER_CONCURRENCY` 控制并发，任务有进度、取消标记、超时和指数退避重试。解析按页、知识点提取按内容块提交，因此失败后可续跑。任务键防止重复入队。人工编辑保留审计记录；修改原文会使关联知识点与题目进入待复核状态。已发布试卷的快照不随题库变化。

开发版首次启动用 SQLAlchemy 建表。正式部署建议增加迁移、外部对象存储、反向代理与 HTTPS，并将卷、密钥和数据库纳入备份。API 文档在 <http://localhost:8000/docs>。

## 验证

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
cd frontend && npm install && npm run build
```

集成测试用 SQLite 和即时执行的 Celery 任务覆盖人工录入路径，不依赖外部模型。实际部署由 MySQL、Redis 和独立 worker 执行任务。自动题目校验采用结构、唯一性和原文逐字依据等确定性检查；语义正确性与是否超纲仍由审核员最终确认。

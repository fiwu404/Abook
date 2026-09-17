# Abook — 教材到考试的闭环 MVP

基于 Vue 3、TypeScript、FastAPI、MySQL、Celery 和 Redis。第一版仅接收 PDF，只生成与审核单选题。流程按 `AGENTS.md` 实现：教材解析、人工校对、内容索引、知识点审核、试题校验和审核、组卷发布、收卷判分、错题重练。

## 启动

```bash
cp .env.example .env
docker compose up --build
```

打开 <http://localhost:5173>。默认开发账号为 `operator/operator1234`（操作员）、`reviewer/reviewer1234`（审核员）。考生在登录页注册。部署前务必修改 `.env` 中的数据库密码、两个初始账号密码和 `APP_SECRET`。

### 模型配置

`MODEL_BASE_URL` 为 OpenAI 兼容的 `/v1` 地址，`TEXT_MODEL` 用于知识点与题目生成，`OCR_MODEL` 用于扫描页识别，`MODEL_API_KEY` 为相应密钥。本地 Ollama 可用 `http://host.docker.internal:11434/v1`；若容器无法解析该主机，需在部署环境配置容器访问地址。也可接入支持相同接口的云端模型或 OCR 视觉模型。

未配置模型时，PDF 的文本页仍可解析，扫描页会标出待处理问题；操作员可人工修正文稿、补充知识点和录入题目。AI 任务会给出明确的配置错误。质量问题不会被静默跳过。

## 工作顺序

1. 操作员上传 PDF，启动解析任务，在“后台任务”查看进度。
2. 在“教材与学习”核对 PDF 页码、印刷页码、章节与 OCR 文本；保存局部修正后确认映射并建立内容索引。未处理的问题继续显示。
3. AI 提取知识点或人工补充；审核员逐条通过或退回。无原文出处的人工补充可入库，但不能出题。
4. 对已审核且有出处的知识点 AI 出题或人工录题。结构、答案、重复和原文依据自动校验；审核员确认题目。
5. 操作员选择知识点、题量、每题分值和难度组卷，核对总分，设置考试时段并发布。发布时冻结题目、答案和评分规则。
6. 考生在时段内答题，答案自动保存；提交或到时判分。成绩包含错题、解析、原文依据及知识点掌握情况。错题可用已审核原题重练；变式题生成后仍需审核员通过。

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

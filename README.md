# AcademicTranslate Agent

专业学术论文翻译智能体：**版面保真 + 内容保真**。

> 看得懂 PDF 结构 · 翻得懂专业知识 · 不破坏公式、图表和数据 · 翻译后仍然像原来的论文

## 产品定位

不是「PDF → 纯文本 → 再生成一份差不多的 PDF」，而是：

1. 解析 PDF Layout Tree（段落 / 图 / 表 / 公式 / 页眉页脚）
2. 只翻译允许翻译的 Text Block
3. 公式、图片、数字、单位、引用编号进入**保护区**
4. 三层 QA（程序校验 → 术语一致性 → 语义审校入口）
5. Layout Composer 回写译文 PDF（结构一致，中文区域自适应重排）

## 技术架构

```
Frontend (React)
  ↓
Agent Layer (FastAPI)
  · DocumentAgent 编排
  · TranslationAgent 翻译
  · QAService 校验
  ↓
LLM Layer (OpenAI 兼容，可配置 GPT / Qwen / 本地模型)
  ↓
Tools & Data
  · PyMuPDF 解析 / 合成
  · 术语库 JSON
  · 本地文件存储
```

## 目录结构

```
Translate-Agent/
├── backend/                 # FastAPI + Agent
│   ├── app/
│   │   ├── agents/          # 编排与翻译 Agent
│   │   ├── api/             # REST API
│   │   ├── services/        # PDF / LLM / QA / 术语
│   │   └── models/          # Schema
│   └── requirements.txt
├── frontend/                # React + Vite 工作台
├── scripts/                 # 本地启停/状态管理脚本
├── manage.bat / start.bat / stop.bat / status.bat / restart.bat
├── docs/                    # 方案设计与原型
└── README.md
```

## 快速开始（推荐：项目管理脚本）

Windows 本地一键管理前后端（初始化 / 启动 / 状态 / 停止 / 重启）：

```bat
manage.bat setup
manage.bat start
manage.bat status
manage.bat stop
```

也可使用 PowerShell：`.\scripts\manage.ps1 start`  
详细说明见 [docs/本地项目管理脚本.md](docs/本地项目管理脚本.md)。

- 前端：<http://127.0.0.1:5173>
- 后端健康检查：<http://127.0.0.1:8080/api/health>
- 日志与 PID：`.runtime/`

首次启动后，请到 Web「模型配置」填写 API Key（或编辑 `backend/.env` / `backend/data/llm_config.json`）。

### 手动启动（可选）

**后端**

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

**前端**

```bash
cd frontend
npm install
npm run dev
```

## 核心页面

| 页面 | 能力 |
| --- | --- |
| 首页 | 单文件 / 批量上传并翻译 PDF（每个≤100MB）、任务列表 |
| 文档翻译工作台 | 原文 PDF / 全文译文 / 全文双语对照、自动保真校验、任务模型记录 |
| 术语库 | 系统词库 + 自定义词库 CRUD |
| 模型配置 | Provider / Model / Base URL / API Key / 输出长度 / 补充领域说明（固定严谨模式） |

## 验收指标（MVP 已落地检测项）

| 指标 | 目标 |
| --- | --- |
| 数字 / 单位保真 | 程序比对 protected tokens |
| 公式保真 | 公式块不送译，原文回写 |
| Fig/Table/引用编号 | 正则提取并校验是否保留 |
| 术语一致率 | 词库命中译法一致性检查 |
| 段落漏译 | 空译文计数 |
| PDF 版式 | 基于原 PDF 红acted 回填，结构高度接近原稿 |

## API 摘要

- `POST /api/documents/upload`
- `POST /api/documents/{id}/translate`
- `GET /api/documents/{id}/result`
- `GET /api/documents/{id}/source.pdf`
- `GET /api/documents/{id}/translated.pdf`
- `GET/PUT /api/llm/config`
- `GET /api/terminology/books`

## 后续可增强

- 更强的版面分析（双栏阅读顺序、公式 OCR/LaTeX）
- 向量库 RAG 做全文问答与术语推荐
- 批量任务队列（Celery / Redis）
- 译文 PDF 中文字体子集嵌入与更精细的自适应排版
- 人工抽检工作流与导出审校报告

## License

MIT

### 翻译可靠性

任务启动时固定模型配置，所有段落使用同一配置。界面记录实际模型，旧任务未记录时不会推测模型。采样温度在服务端固定为 0；配置中旧的 temperature 字段被忽略。空回复、截断和接口错误会使任务失败，不再将失败提示和原文伪装为译文。自动校验不等于人工语义审校。


### PDF 版面保真阅读（v2）

- 阅读页显示由实际原文 / 译文 PDF 渲染的整页，双栏、图表、公式随 PDF 保留。支持同步翻页、连续阅读、缩放、下载。
- 原生文本 PDF 使用 PyMuPDF 提取坐标与字体，在原文件副本上仅替换文字。使用 HTML / HarfBuzz 实际试排，统一清除原文字后写入译文，避免重复擦除。
- 数学公式区域保留原件。历史译文的行内 LaTeX 使用 Matplotlib MathText 在本地渲染；没有使用远程视觉模型重画公式。作者照片保留，文字避让照片区域。
- 导出前检查字号下限、字符完整性、页面尺寸、文字区重叠，并对识别出的保护区域作像素比较。无法安全放入的段落保留原文且列入排版报告；保护区损坏会阻止导出。
- 旧任务点击“重新排版”，复用已有译文，不产生模型调用。`POST /api/documents/{id}/rebuild-layout`；整页预览 `GET /api/documents/{id}/pages/{page}.png?kind=source|target`。
- 这不是对任意 PDF 的绝对版面保证：扫描件仍不支持；复杂未识别的混排和学术语义需人工核对。MinerU / LayoutParser 可用于未来的 OCR / 版面识别扩展，不能替代回填和导出检查。

验证：后端 `python -m unittest discover -s tests -v`；前端 `npm run build`。`frontend/checks/pdf-browser-check.mjs` 是对本地示例论文和服务运行的浏览器检查，不调用翻译模型；加 `--rebuild` 可验证已有译文重排。


### 局域网访问与逐页展示

- 前后端默认均监听 `0.0.0.0`，端口保持前端 `5173`、后端 `8080`。前端开发和预览服务接受任意 Host；端口占用时直接报错，不再自动换端口。
- 本机访问 `http://127.0.0.1:5173`；其他设备访问 `http://<运行服务的机器 IP>:5173`。`0.0.0.0` 是监听地址，不是浏览器访问地址。启动脚本的健康检查仍使用本机回环地址。
- 修改后使用 `restart.bat` 或 `manage.bat restart` 重启。若系统防火墙拦截，需允许相应端口的入站访问。
- 翻译按页执行：每页翻译、校验及排版完成后即发布，该页可以立即阅读。页面约每秒检查一次进度；未完成的页面显示等待状态。全文完成后可下载合并 PDF。
- 已完成页面保存在 `backend/data/docs/{id}/runs/{run_id}/page-{n}.pdf`；任务失败仍可查看已完成页。重试生成新的任务标识并清空当前进度，避免显示上一轮旧译文。
- 文档接口增加 `translation_run_id`、`completed_pages`、`current_page`，翻译过程中也返回已完成页面的结果。整页预览支持可选的 `run_id`，旧任务页面请求返回 409。

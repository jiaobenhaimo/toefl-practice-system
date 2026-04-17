# 📝 TOEFL 模拟练习系统

**[English](README.md)** | **[中文](README.zh.md)**

面向 **2026 TOEFL iBT** 的自托管模拟考试平台——服务端评分、1–6 分段实时出分、完整的教师管理后台。一条命令启动，无需外部数据库。

> 由[超能录](https://github.com/jiaobenhaimo)使用 [Claude](https://claude.ai)（Anthropic）通过多轮对话协作开发。

## 🌟 亮点

- **七种题型** — 选择题、完形填空、造句、邮件写作、学术讨论、跟读、口语面试
- **2026 TOEFL 评分** — 采用 ETS 官方分数对照表（口语 /55、写作 /20、阅读与听力 /30 → 1.0–6.0 分段）
- **答案不发送至浏览器** — 服务端评分保障考试安全
- **跨设备续考** — 电脑上开始、手机上完成，进度实时同步
- **间隔重复** — 错题自动进入复习队列，按 1→3→7→14 天间隔复习
- **教师工具** — 布置测试（支持时间窗口）、评分（0–5 评分标准）、逐题批注、实时监控、追踪进度
- **家长面板** — 只读查看孩子的成绩、分析和教师反馈
- **分项分析** — 成绩趋势图分别显示阅读、听力、写作、口语四条曲线
- **深色模式** — 自动跟随系统设置，支持手动切换
- **一键启动** — 仅需 Python + SQLite，无需安装其他服务
- **原生 macOS 客户端** — 可在 `TOEFLClient/` 仓库获取 SwiftUI 桌面应用

## ⬇️ 安装

```bash
git clone https://github.com/jiaobenhaimo/toefl-practice-system.git
cd toefl-practice-system
pip install -r requirements.txt
python app.py
```

打开 `http://localhost:8080`。默认账号：`admin` / `admin`。**请立即在 `/admin/users` 修改密码。**

管理员创建的新用户默认密码为 `12345678`，用户应在首次登录后立即通过账户页面修改密码。

需要 Python 3.9 及以上版本。支持 macOS、Linux 和 Windows。口语录音功能需要 HTTPS（或 localhost）。

### 生产环境部署

```bash
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

在前端配置 Nginx 或 Caddy 并启用 SSL，以支持麦克风权限。

## 🚀 使用方法

### 学生

登录 → 查看**作业** → 开始考试 → 即时获得 **1–6 分段成绩** → 查看错题解析和教师批注。错题自动进入**复习队列**进行间隔重复。

### 教师

**提交记录** — 浏览所有学生的考试结果，回放口语录音，阅读写作答案。**布置测试** — 选择学生、试卷，可指定分项、截止日期和时间窗口（开放时间/截止时间）。**评分** — 为口语和写作题目打 0–5 分。**进度追踪** — 一览学生完成率，点击查看详细的历史记录和分析图表。**实时监控** — 查看正在考试的学生及其实时进度。

### 家长

只读查看孩子的考试历史、分析数据、分段成绩和教师反馈。管理员将家长账户与学生关联。

### 管理员

拥有教师的全部权限，另可管理用户（创建、编辑、删除、CSV 批量导入）及发布全站公告。

## 📐 评分方式（2026 TOEFL iBT）

系统采用 ETS 2026 年 1 月改革后的官方评分表：

| 分项 | 评分方式 | 分段示例 |
|---|---|---|
| **阅读 / 听力** | 正确率 → 估算 0–30 | 29–30 → 6.0 · 24–26 → 5.0 · 18–21 → 4.0 |
| **写作** | 造句（每题 1 分，自动评分）+ 邮件与讨论（评分标准 0–5）→ /20 | 19–20 → 6.0 · 15–16 → 5.0 · 11–12 → 4.0 |
| **口语** | 跟读 + 面试（评分标准 0–5）→ /55 | 52–55 → 6.0 · 42–46 → 5.0 · 32–36 → 4.0 |
| **总分** | 四项分段分数的平均值，四舍五入至最近的 0.5 | |

练习卷题量少于正式考试时，系统会等比缩放至官方分值范围后查表。

## ✍️ 创建试题

将 `.md` 文件放入 `tests/` 文件夹：

```markdown
---
test_name: "阅读练习 1"
test_type: toefl
---
# Reading — Module 1 — 18 min

## Passage
The quick brown fox...

[question]
What does the passage mainly discuss?
- A) Foxes
- B) Speed
- C) Colors
- D) Animals
answer: A
[/question]
```

音频文件放在与试卷同名的子文件夹中（例如 `tests/listening-1/track01.ogg`）。示例试卷中包含全部 7 种题型的写法。

在题目后添加 `[explanation]...[/explanation]` 可提供解析，提交后向学生展示。教师也可在复习页面添加或覆盖解析。

## 🏗️ 项目结构

```
app.py              Flask 服务端 — 路由、中间件、初始化
helpers.py          公共工具 — 缓存、认证装饰器、TOEFL 评分、配置
database.py         SQLite — 9 张表，线程本地连接池
parser.py           Markdown → 结构化试题数据
static/js/app.js    考试引擎 — 计时、音频、录音、键盘导航
static/css/style.css   Apple HIG 设计体系，明暗模式
templates/          15 个 Jinja2 模板
tests/              试题 Markdown 文件 + 音频
data/               SQLite 数据库 + 录音文件（自动创建）
docs/               用户手册
authoring/          试题格式说明 + TTS 音频生成器
```

**安全性** — 全部表单启用 CSRF 保护 · 登录频率限制 · 会话重新生成 · API 不返回答案 · 录音访问控制 · 16 MB 上传限制 · 防开放重定向。

**性能** — 线程本地数据库连接 · 请求级用户缓存（flask.g）· 试卷解析按文件修改时间缓存 · 进度追踪使用批量 SQL 查询 · 错题本批量更新 · 音频压缩为 32 kbps 单声道 Opus · 实时监控指数退避轮询。

## ⚙️ 配置

所有设置集中在 `config.yaml`：

```yaml
site:
  name: "我的考试平台"

default_admin:
  username: admin
  password: admin
```

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `TOEFL_TESTS_DIR` | `./tests` | 试题 `.md` 文件目录 |
| `TOEFL_DB_PATH` | `./data/toefl.db` | SQLite 数据库路径 |
| `SECRET_KEY` | 随机生成 | Flask 会话加密密钥 |

## 📡 API 参考

服务端暴露 JSON API，驱动网页前端和任何外部客户端。所有端点位于 `/api/` 下，通过 JSON 通信。

### 认证机制

API 使用 **Flask 会话 Cookie**。登录后服务端设置会话 Cookie，HTTP 客户端自动存储。后续请求携带该 Cookie 即视为已认证——无需 API Key 或 Bearer Token。

**浏览器客户端**（内置网页 UI）的 CSRF 保护通过会话自动完成。**非浏览器客户端**（如原生应用）的 JSON POST 请求通过验证 `Origin` 头部替代 CSRF 令牌——因此所有 POST 请求必须发送 `Content-Type: application/json` 并携带会话 Cookie。

**频率限制**：登录限制为每个 IP 地址在 5 分钟内最多 10 次尝试。超出后返回 `429 Too Many Requests`。

#### 登录

```
POST /api/auth/login
Content-Type: application/json

{"username": "student1", "password": "mypassword"}
```

**成功响应 (200)：**

```json
{
  "ok": true,
  "user": {"id": 2, "username": "student1", "display_name": "Alice Chen", "role": "student"}
}
```

**错误响应 (401)：** `{"ok": false, "error": "invalid_credentials"}`

响应包含 `Set-Cookie` 头部。HTTP 客户端必须存储并在后续请求中重发此 Cookie。

#### 检查当前会话

```
GET /api/auth/me
```

已认证返回用户信息，未认证返回 `401`。

#### 登出

```
POST /api/auth/logout
Content-Type: application/json
{}
```

清除会话，返回 `{"ok": true}`。

### 考试库

```
GET /api/catalog
```

返回所有试卷及其分项、模块列表，含文件名、模块索引、分项名、计时等。

### 加载试题

```
GET /api/module/<filename>?module_index=0
GET /api/module/<filename>?module_index=0&practice=true
```

正常模式不含答案，练习模式含答案可即时自查。返回 `pages` 数组，每个元素为一道题，字段因题型而异。

### 评分

```
POST /api/grade
Content-Type: application/json

{
  "filename": "practice-1.md",
  "module_index": 0,
  "answers": {"r1q1": "A", "r1q2": "C"},
  "times": {"r1q1": 45, "r1q2": 30}
}
```

服务端评分后返回每题的对错（`correct: true/false`），但**不返回正确答案**。

### 会话管理（跨设备续考）

```
POST /api/session/start        # 开始或恢复考试
POST /api/session/<id>/save    # 自动保存进度
POST /api/session/<id>/advance # 进入下一模块
POST /api/save-results         # 保存最终成绩（服务端重新验证所有答案）
```

**时间窗口控制**：如果学生有含时间窗口的作业，服务端会检查当前时间是否在窗口内。窗口外返回 `403`（`not_yet_available` 或 `schedule_expired`）。练习模式不受限制。

### 复习与评分

```
GET  /api/review-data/<id>          # 完整复习数据
GET  /api/toefl-scores/<id>         # 1–6 分段成绩
POST /api/rubric-score/<id>         # 保存评分标准分数 (0–5，草稿)
POST /api/rubric-submit/<id>        # 发布评分（学生可见，创建通知）
```

### 数据分析

```
GET /api/analytics/<user_id>
```

返回 `score_history`（含每次考试的 `section_bands`，用于分项趋势图）和 `section_breakdown`（各分项汇总成绩）。

### 成绩历史

```
GET /api/my-history?page=1
```

返回分页的成绩列表，每条包含 `band_overall`、`band_sections`、`needs_rubric` 字段。

### 作业

```
GET /api/my-assignments
```

返回待完成作业，含 `schedule_start`/`schedule_end` 时间窗口。

### 通知

```
GET  /api/notifications           # 未读通知
POST /api/notifications/read      # 标记已读，{"ids": [1, 2]}
```

### 间隔重复

```
GET  /api/review-queue            # 待复习题目
GET  /api/review-count            # 数量（用于角标）
POST /api/review-answer/<id>      # 提交复习答案，{"correct": true/false}
```

### 其他端点

| 方法 | 端点 | 说明 |
|---|---|---|
| GET/POST | `/api/notes/<result_id>` | 学生笔记 |
| GET/POST | `/api/comments/<result_id>` | 教师批注 |
| GET/POST | `/api/explanations/<test_id>` | 题目解析 |
| GET | `/api/export-pdf/<result_id>` | 下载 PDF 报告 |
| GET | `/recordings/<result_id>/<qid>` | 播放学生录音 |
| GET | `/api/live-sessions` | 活动会话（仅教师/管理员） |

### 错误码

| 状态码 | 含义 |
|---|---|
| 200 | 成功 |
| 400 | 请求格式错误 |
| 401 | 未认证（会话过期或缺失） |
| 403 | 无权限（角色不符、时间窗口限制、CSRF 失败） |
| 404 | 资源不存在 |
| 429 | 请求过于频繁（登录限制） |

## 💭 反馈与贡献

发现 bug 或有新想法？欢迎[提交 Issue](https://github.com/jiaobenhaimo/toefl-practice-system/issues) 或 Pull Request。

## 📜 许可证

GNU 通用公共许可证 v3.0 — 见 [LICENSE](LICENSE)。

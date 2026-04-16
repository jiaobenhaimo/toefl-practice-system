# 📝 TOEFL 模拟练习系统

**[English](README.md)** | **[中文](README.zh.md)**

面向 **2026 TOEFL iBT** 的自托管模拟考试平台——服务端评分、1–6 分段实时出分、完整的教师管理后台。一条命令启动，无需外部数据库。

> 由[超能录](https://github.com/jiaobenhaimo)使用 [Claude](https://claude.ai)（Anthropic）通过多轮对话协作开发。

## 🌟 亮点

- **七种题型** — 选择题、完形填空、造句、邮件写作、学术讨论、跟读、口语面试
- **2026 TOEFL 评分** — 采用 ETS 官方分数对照表（口语 /55、写作 /20、阅读与听力 /30 → 1.0–6.0 分段）
- **答案不发送至浏览器** — 服务端评分保障考试安全
- **跨设备续考** — 电脑上开始、手机上完成，进度实时同步
- **教师工具** — 布置测试、评分（0–5 评分标准）、逐题批注、追踪学生进度
- **一键启动** — 仅需 Python + SQLite，无需安装其他服务

## ⬇️ 安装

```bash
git clone https://github.com/jiaobenhaimo/toefl-practice-system.git
cd toefl-practice-system
pip install -r requirements.txt
python app.py
```

打开 `http://localhost:8080`。默认账号：`admin` / `admin`。**请立即在 `/admin/users` 修改密码。**

需要 Python 3.9 及以上版本。支持 macOS、Linux 和 Windows。口语录音功能需要 HTTPS（或 localhost）。

### 生产环境部署

```bash
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

在前端配置 Nginx 或 Caddy 并启用 SSL，以支持麦克风权限。

## 🚀 使用方法

### 学生

登录 → 查看**作业** → 开始考试 → 即时获得 **1–6 分段成绩** → 查看错题解析、教师批注和个人笔记。

### 教师

**提交记录** — 浏览所有学生的考试结果，回放口语录音，阅读写作答案。**布置测试** — 选择学生、试卷，可指定分项和截止日期。**评分** — 为口语和写作题目打 0–5 分。**进度追踪** — 一览学生完成率，点击查看详细的历史记录和分析图表。

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

## 🏗️ 项目结构

```
app.py              Flask 服务端 — 认证、评分、会话、仪表盘
database.py         SQLite — 8 张表，全部 CRUD
parser.py           Markdown → 结构化试题数据
static/js/app.js    考试引擎 — 计时、音频、录音、键盘导航
static/css/style.css   Apple HIG 设计体系，明暗模式
templates/          13 个 Jinja2 模板
tests/              试题 Markdown 文件 + 音频
data/               SQLite 数据库 + 录音文件（自动创建）
```

**安全性** — 全部表单启用 CSRF 保护 · 登录频率限制 · 会话重新生成 · API 不返回答案 · 录音访问控制 · 16 MB 上传限制 · 防开放重定向。

**性能** — 请求级缓存（用户、公告） · 试卷解析按文件修改时间缓存 · 进度追踪使用批量 SQL 查询（共 2 条查询，而非 2×N 条）。

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

## 📡 API

<details>
<summary>点击展开 API 参考</summary>

### 考试引擎

| 方法 | 端点 | 说明 |
|---|---|---|
| GET | `/api/module/<filename>` | 加载试题（答案已过滤） |
| POST | `/api/grade` | 服务端评分 |
| POST | `/api/save-results` | 保存评分结果 |

### 会话管理

| 方法 | 端点 | 说明 |
|---|---|---|
| POST | `/api/session/start` | 开始或恢复考试 |
| POST | `/api/session/<id>/save` | 自动保存进度 |
| POST | `/api/session/<id>/advance` | 评分后进入下一模块 |
| GET | `/api/session/<id>` | 加载会话状态 |
| DELETE | `/api/session/<id>` | 放弃会话 |

### 复习与评分

| 方法 | 端点 | 说明 |
|---|---|---|
| GET | `/api/review-data/<id>` | 完整复习数据（含评分） |
| GET | `/api/toefl-scores/<id>` | 1–6 分段成绩 |
| POST | `/api/rubric-score/<id>` | 保存评分标准分数（0–5） |
| GET/POST | `/api/notes/<id>` | 学生笔记 |
| GET/POST | `/api/comments/<id>` | 教师批注 |
| GET/POST | `/api/explanations/<test_id>` | 题目解析 |
| GET | `/api/analytics/<uid>` | 成绩历史与分项分析 |
| GET | `/api/export-pdf/<id>` | 下载 PDF 报告 |

</details>

## 💭 反馈与贡献

发现 bug 或有新想法？欢迎[提交 Issue](https://github.com/jiaobenhaimo/toefl-practice-system/issues) 或 Pull Request。

## 📜 许可证

GNU 通用公共许可证 v3.0 — 见 [LICENSE](LICENSE)。

**[English](README.md)** | **[中文](README.zh.md)**

# 在线模考系统

支持用户认证、服务端评分、角色面板和多考试类型的在线模拟考试平台。目前用于 TOEFL 练习，架构支持任何标准化考试格式。

> 本项目主要通过 [Claude](https://claude.ai) (Anthropic) vibe coding 开发。架构、代码和文档均通过迭代对话生成。

---

## 部署

```bash
git clone <repo-url> && cd toefl-practice-system

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
python app.py                    # http://localhost:8080
```

首次运行自动创建数据库和默认账号（见 `config.yaml`）。

### 生产部署

```bash
source venv/bin/activate
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

配置 Nginx/Caddy + SSL。

### 配置

`config.yaml` 定义站点名称、考试类型、默认账号。环境变量 `TOEFL_TESTS_DIR`、`TOEFL_DB_PATH`、`SECRET_KEY` 可覆盖。

---

## 架构

- **SQLite** 单文件数据库（用户、成绩、分配）
- **服务端评分**（答案不发送到客户端）
- **多考试类型**（config.yaml 定义，支持并行）
- **四种角色**：管理员、教师、学生、访客

---

## 功能

- 七种题型、练习模式、计时器
- 侧边栏导航（桌面固定、移动端汉堡菜单）
- 自适应卡片网格、模态选择框
- 管理面板（创建/编辑/删除用户、确认对话框、角色切换按钮）
- 教师面板（成绩查看、按部分分配测试、学生进度追踪）
- 自助密码修改、深色模式、中英文界面
- PDF 导出、ZIP 下载、每题计时

---

## 试卷编写

工具在 `authoring/` 目录。详见 `authoring/FORMAT.md`。

## 许可证

GPL v3。详见 [LICENSE](LICENSE)。

**[English](README.md)** | **[中文](README.zh.md)**

# 超能录 托福模拟考试系统

基于 Flask 和原生 JavaScript 的托福在线模拟考试平台。教师使用自定义 Markdown 格式编写试题，系统自动解析并生成交互式考试界面，支持计时、自动评分、音频播放和麦克风录音。

本项目是**超能录**辅导团队的组成部分，提供 AP/A-Level 课程、学科竞赛、JLPT、TOPIK 及 TOEFL/IELTS 辅导。

## 环境要求

- Python 3.9 或更高版本
- 现代浏览器（Chrome、Firefox、Safari 或 Edge）
- 口语部分需要麦克风及浏览器录音权限

## 安装

```bash
cd toefl-system
pip install -r requirements.txt
```

## 启动服务器

```bash
# 默认在 8080 端口启动
python app.py

# 自定义端口
python app.py --port 3000
```

在浏览器中打开 `http://localhost:8080`。

### 生产环境部署

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

非 localhost 环境下麦克风录音必须使用 HTTPS。请配置反向代理（Nginx、Caddy）并添加 SSL 证书。

### Docker 部署

```bash
docker build -t toefl-practice .
docker run -p 8080:8080 -v /path/to/tests:/app/tests toefl-practice
```

## 使用说明

### 教师：创建试卷

1. 按照 `FORMAT.md` 规范编写 `.md` 试题文件。
2. 听力和口语部分需准备 `.ogg` 音频文件，放入与 `.md` 文件同名（去掉后缀）的文件夹中。
3. 将文件放入 `tests/` 目录，刷新目录页即可看到新试卷。

单个 `.md` 文件可以包含同一部分的多个模块（如阅读 Module 1 和 Module 2），也可以包含所有四个部分。多个 `.md` 文件如果 `test_id` 相同，系统会自动合并为同一套试卷。

### 教师：生成 TTS 音频

```bash
python generate_tts_notebook.py tests/*.tts -o tts_generate.ipynb
```

将生成的 `.ipynb` 上传到 Google Colab，选择 T4 GPU 运行时，运行所有单元格。使用 Kokoro TTS 引擎，女声为 `af_heart`，男声为 `am_fenrir`。

### 学生：参加考试

1. 打开 `http://localhost:8080`，点击试卷卡片展开。
2. 选择**完整考试**（所有部分按顺序进行）或**单独某个部分**（如阅读）。选择某个部分会连续完成该部分的所有模块。
3. 逐题作答。倒计时剩余 5 分钟时变为琥珀色，剩余 1 分钟时变为红色并闪烁。
4. 阅读部分允许回到上一题，其他部分只能向前。
5. 听力部分音频自动播放一次，不能重播。
6. 口语部分先听完提示音频，录音按钮激活后开始录音。
7. 写作部分在文本框中输入，下方实时显示字数。
8. 模块之间的过渡页面只显示"部分完成"和下一部分信息，不显示分数。所有分数在最终成绩页面统一展示。
9. 成绩页面按部分显示评分明细。填空题逐空显示对错（绿色/红色）。点击**下载答案 (.zip)** 获取文字答案和口语录音。

系统每 30 秒自动保存答题进度。口语录音无法跨会话保存。

### 填空题（cloze）格式

填空使用 `前缀[N]后缀` 语法，其中 N 为缺失字母数：

```
manu[7]     → 输入 7 个字母 ("scripts") → "manuscripts"
centu[4]    → 输入 4 个字母 ("ries")    → "centuries"
un[5]able   → 输入 5 个字母 ("avoid")   → "unavoidable"
```

每个空位渲染为 N 个独立的字符输入框。填满后光标自动跳转到下一个空位，从最后一个空位循环回到第一个。

## 项目结构

```
toefl-system/
  app.py                    Flask 服务器（路由、缓存、路径安全）
  parser.py                 Markdown 试题解析器
  generate_tts_notebook.py  Colab 笔记本生成器（TTS 音频）
  requirements.txt          flask, pyyaml, markdown
  FORMAT.md                 Markdown 格式规范
  LICENSE                   GPL v3 许可证
  templates/                Jinja2 模板（目录页、考试页）
  static/css/style.css      浅色主题界面（遵循 Apple HIG）
  static/js/app.js          考试引擎
  tests/                    试题内容（*.md、*.tts、音频文件夹）
```

## 设计规范

界面遵循 Apple Human Interface Guidelines：系统字体栈（SF Pro / system-ui）、44pt 最小触控目标、简洁的白色/浅灰色调色板、语义化颜色、0.3 秒以内的动画过渡。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `TOEFL_TESTS_DIR` | `<脚本目录>/tests` | 试题文件和音频目录 |

## 已知限制

- 客户端评分：正确答案在 API 响应中可见。
- 口语录音在关闭浏览器后丢失。
- 无用户认证；进度存储在浏览器 localStorage 中。
- 填空题精确匹配评分（不区分大小写）。

## 许可证

本项目采用 **GNU 通用公共许可证第三版（GPL v3）** 授权。详见 [LICENSE](LICENSE)。任何衍生作品必须以相同许可证发布。

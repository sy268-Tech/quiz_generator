# CLAUDE.md

## 项目概述
智能出题系统 — 上传 Word/PDF 文档，AI 自动分析内容并生成题目，用户在界面中作答，系统自动判分。

## 技术栈
- **UI**: Streamlit
- **文档解析**: python-docx (Word) + pdfplumber (PDF)
- **出题引擎**: Anthropic API（Claude Sonnet 4.6）
- **数据持久化**: SQLite3
- **数据处理**: Pandas（统计图表）

## 文件结构
```
quiz_generator/
  app.py                 # Streamlit UI 入口
  document_parser.py     # Word/PDF/TXT 文本提取
  question_generator.py  # Anthropic API 出题逻辑
  database.py            # SQLite 数据持久化
  requirements.txt       # 依赖
  quiz.db                # 自动生成的数据库文件
  CLAUDE.md              # 本文件
```

## 依赖关系
```
app.py → document_parser.py (parse_document)
app.py → question_generator.py (generate_questions)
app.py → database.py (CRUD 函数)
question_generator.py → anthropic SDK
database.py → sqlite3 (标准库)
```

## 数据库表结构
```sql
quizzes (id, document_name, total_questions, difficulty, questions_json, created_at)
attempts (id, quiz_id, user_answers_json, score, total_scored, created_at)
wrong_questions (id, quiz_id, question_json, user_answer, wrong_count, last_wrong_at)
```

## 运行方式
```bash
cd quiz_generator
pip install -r requirements.txt
streamlit run app.py
```

## 用户流程
1. 上传文档（.docx/.pdf/.txt/.md）
2. 在侧边栏设置总题数、难度、额外偏好，填入 API Key
3. 点击「生成题目」→ AI 分析内容，自主判断题型并出题
4. 在答题卡中逐题作答
5. 提交 → 自动判分，显示正确答案 + 解析
6. 错题自动收集到错题本，可重做

## 核心设计
- **题型由 AI 自主决定**：系统不预设题型列表，模型分析文档后自行判断每道题适合什么类型
- **动态渲染**：UI 根据每道题的 type 字段匹配对应的输入组件
- **自动判分**：选择题/判断题精确匹配，填空题模糊匹配，配对题逐项匹配，简答题/未知题型不自动判分
- **错题本**：答错的题自动入库，重复做错累加计数，支持按题型筛选和重做

## API 配置
- 用户在侧边栏输入 API Key（支持 Anthropic 官方和兼容的第三方 API）
- 可选配置 Base URL（代理/兼容 API）和模型名称
- 默认模型：claude-sonnet-4-6

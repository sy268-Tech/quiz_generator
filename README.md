# 智能出题系统

上传 Word/PDF 文档，AI 自动分析内容生成题目，在线作答并自动判分。支持错题收集与重做。

## 功能

- **多格式文档解析** — 支持 `.docx` / `.pdf` / `.txt` / `.md`，可同时上传多个文件
- **AI 自主出题** — 模型分析文档内容后自行判断每道题适合什么题型（选择/填空/判断/简答/编程/配对等）
- **多模型支持** — Anthropic Claude、DeepSeek、OpenAI GPT、通义千问、智谱 GLM、Moonshot，也可自定义兼容接口
- **在线答题** — 根据题型动态渲染对应的输入组件（单选、填空、文本域、下拉配对等）
- **自动判分** — 选择题/判断题精确匹配，填空题模糊匹配，配对题逐项匹配；简答题/编程题标注需手动检查
- **错题本** — 答错的题自动入库，重复做错累加计数，支持按题型筛选和重做
- **学习统计** — 试卷数、答题次数、错题数、正确率趋势图

## 快速开始

```bash
cd quiz_generator
pip install -r requirements.txt
streamlit run app.py
```

浏览器打开 `http://localhost:8501`。

## 使用流程

1. 在侧边栏选择模型服务商，填入 API Key
2. 设置难度等级、各题型数量（设为 0 则不出该题型）
3. 上传文档，点击「生成题目」
4. 在答题卡中逐题作答，提交后自动判分
5. 错题自动进入错题本，可随时重做

也可以启用「不调用大模型」模式，直接从文档提取填空题，无需 API Key。

## 技术栈

| 组件 | 技术 |
|------|------|
| UI | Streamlit |
| 文档解析 | python-docx + pdfplumber |
| 出题引擎 | Anthropic API / OpenAI 兼容接口 |
| 数据存储 | SQLite3 |
| 数据处理 | Pandas |

## 文件结构

```
quiz_generator/
├── app.py                 # Streamlit UI 入口
├── document_parser.py     # Word/PDF/TXT 文本提取
├── question_generator.py  # LLM API 出题逻辑
├── text_extractor.py      # 无 LLM 直接提取填空题
├── database.py            # SQLite 数据持久化
├── requirements.txt       # 依赖
└── CLAUDE.md              # AI 编码指引
```

## 依赖关系

```
app.py → document_parser.py (parse_document)
app.py → question_generator.py (generate_questions)
app.py → text_extractor.py (extract_sequential)
app.py → database.py (CRUD)
question_generator.py → anthropic / openai SDK
```

## 数据库表

- **quizzes** — 试卷信息 + 题目 JSON
- **attempts** — 答题记录 + 得分
- **wrong_questions** — 错题收集（自动去重，累加错误次数）

## License

MIT

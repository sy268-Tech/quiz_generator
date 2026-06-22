"""多模型出题引擎 — 支持 Anthropic 原生 + OpenAI 兼容接口，题型由模型自主判断。"""

import json
import time

import anthropic
from openai import OpenAI

SYSTEM_PROMPT = """你是一位经验丰富的出题老师。你的任务是根据提供的文档内容出题。

## 核心要求
1. **题型由你自行决定**：分析文档内容后，自行判断每道题适合出成什么类型。可以是单选题、多选题、填空题、判断题、简答题、配对题、排序题、连线题……只要你觉得合理，什么题型都可以。在每道题的 `type` 字段中写明题型名称（选择题必须明确标注"单选题"或"多选题"，不要笼统写"选择题"）。
2. **严格基于文档内容**：所有题目必须能从文档中找到依据，不能凭空编造。
3. **难度分级**：每道题标注 `difficulty`（简单/中等/困难），根据题目考察的深度和复杂性判断。
4. **答案必须准确**：答案必须与文档内容一致。
5. **答案解析必填**：每道题必须附带 `explanation` 字段，说明为什么是这个答案，引用文档中的相关内容。

## 返回格式
你必须只返回一个 JSON 对象，不要包含任何 markdown 代码块标记或其他文字。格式如下：

{
  "question_types_used": ["选择题", "填空题", ...],
  "questions": [
    {
      "id": 1,
      "type": "单选题",
      "difficulty": "中等",
      "question": "题目内容",
      "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"},
      "answer": "A",
      "explanation": "答案解析，说明为什么选A，引用文档相关段落"
    },
    {
      "id": 2,
      "type": "多选题",
      "difficulty": "中等",
      "question": "以下哪些是正确的？（多选）",
      "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"},
      "answer": "ABD",
      "explanation": "答案解析，说明为什么选A、B、D"
    },
    {
      "id": 3,
      "type": "填空题",
      "difficulty": "简单",
      "question": "包含______的题目",
      "answer": "填空答案",
      "explanation": "答案解析"
    },
    {
      "id": 4,
      "type": "判断题",
      "difficulty": "简单",
      "question": "需要判断正误的陈述",
      "answer": true,
      "explanation": "答案解析，说明正确或错误的原因"
    },
    {
      "id": 5,
      "type": "简答题",
      "difficulty": "困难",
      "question": "需要详细回答的问题",
      "answer": "参考答案要点",
      "explanation": "答案解析，列出关键得分点"
    }
  ]
}

## 各题型字段说明
- **单选题**：必须有 `options` 对象（键为 A/B/C/D），`answer` **必须严格是单个字母**（"A"、"B"、"C"、"D" 之一），绝对不能包含选项文字或其他内容。只写字母。
- **多选题**：必须有 `options` 对象（键为 A/B/C/D/E/F），`answer` **必须严格是连续字母字符串**（如 "AB"、"ACD"），按字母顺序排列，不含空格或分隔符。题目中应明确提示"多选"。
- **填空题**：题目中用 `______` 表示空白处，`answer` 为正确答案文本。
- **判断题**：`answer` **必须严格是布尔值** `true` 或 `false`，不能用中文 "正确"/"错误" 等字符串替代。
- **简答题/论述题**：`answer` 为参考答案要点。
- **配对题**：必须有 `pairs` 对象，`answer` 为正确的配对关系。
- **编程题**：提供 `coding_prompt`（编程题目描述）、`sample_input`（示例输入）、`sample_output`（示例输出），`answer` 为参考代码。此题型不自动判分，用户自行对比代码。
- **其他自定义题型**：自行设计合理的字段结构。

## 注意事项
- JSON 必须严格合法（双引号、无尾随逗号）。
- 题目 ID 从 1 开始连续编号。
- `question_types_used` 列出本次用到的所有题型名称。
"""


def generate_questions(
    content: str,
    config: dict,
    api_key: str,
    base_url: str | None = None,
    model: str = "claude-sonnet-4-6",
    api_type: str = "anthropic",
) -> dict:
    """调用 LLM API 生成题目，支持 Anthropic 原生和 OpenAI 兼容接口。

    Args:
        content: 文档文本内容
        config: {
            "choice": 10, "true_false": 5, "fill_in_blank": 5,
            "short_answer": 5, "coding": 0,
            "difficulty": "中等", "preference": ""
        }
        api_key: API 密钥
        base_url: 自定义 API 地址
        model: 模型名称
        api_type: "anthropic" | "openai"

    Returns:
        {"question_types_used": [...], "questions": [...]}
    """
    difficulty = config.get("difficulty", "中等")
    preference = config.get("preference", "")

    user_prompt = _build_user_prompt(content, config)

    max_retries = 2
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            if api_type == "anthropic":
                raw_text = _call_anthropic(api_key, model, user_prompt)
            else:
                raw_text = _call_openai(api_key, base_url or "", model, user_prompt)
            return _parse_response(raw_text)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(3)

    raise last_error


def _call_anthropic(api_key: str, model: str, user_prompt: str) -> str:
    """Anthropic Messages API 原生调用。"""
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        temperature=0.7,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


def _call_openai(api_key: str, base_url: str, model: str, user_prompt: str) -> str:
    """OpenAI 兼容 Chat Completions API 调用。"""
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        max_tokens=8192,
        temperature=0.7,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content


def _build_user_prompt(content: str, config: dict) -> str:
    """构建发送给模型的用户消息，包含各题型目标数量。"""
    choice = config.get("choice", 0)
    true_false = config.get("true_false", 0)
    fill_in_blank = config.get("fill_in_blank", 0)
    short_answer = config.get("short_answer", 0)
    coding = config.get("coding", 0)
    difficulty = config.get("difficulty", "中等")
    preference = config.get("preference", "")

    parts = [
        "请根据以下文档内容出题，各题型目标数量如下：",
        f"- 选择题：{choice} 道",
        f"- 判断题：{true_false} 道",
        f"- 填空题：{fill_in_blank} 道",
        f"- 简答题：{short_answer} 道",
        f"- 编程题：{coding} 道",
        "请根据文档内容的实际适合程度灵活调整各题型数量（可在目标值 ±30% 范围内浮动），数量为 0 的题型不要出题。",
        f"整体难度等级：{difficulty}",
    ]
    if preference:
        parts.append(f"额外要求：{preference}")

    # 出题顺序
    sequential = config.get("sequential", False)
    if sequential:
        parts.append("请按照文档内容的出现顺序出题（前面的题目考察文档前部内容，后面的题目考察文档后部内容），题号顺序应与文档内容顺序一致。")
    else:
        parts.append("题目顺序可以随机排列，不要求按照文档内容顺序。")

    parts.append(f"\n文档内容：\n{content}")

    return "\n".join(parts)


def _parse_response(raw_text: str) -> dict:
    """从模型返回的文本中提取并解析 JSON。"""
    text = raw_text.strip()

    # 去掉 markdown 代码块标记
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    # 找到 JSON 对象的起止位置
    start = text.find("{")
    end = text.rfind("}") + 1

    if start == -1 or end == 0:
        raise ValueError(f"模型返回内容中未找到合法 JSON。原始返回:\n{raw_text[:500]}")

    json_str = text[start:end]
    return json.loads(json_str)

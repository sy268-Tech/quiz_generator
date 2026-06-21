"""无 LLM 的按顺序出题 — 从文档中直接提取填空题。"""

import re


def extract_sequential(content: str, config: dict) -> list[dict]:
    """从文档按顺序提取填空题，不调用 LLM。提取所有可用句子。

    返回格式与 generate_questions 一致：[{id, type, difficulty, question, answer, explanation}, ...]
    """
    sentences = _split_sentences(content)
    if not sentences:
        return []

    questions = []
    for sentence in sentences:
        q = _make_fill_blank(sentence, len(questions) + 1)
        if q:
            questions.append(q)

    # 最多 100 道，避免太极端
    return questions[:100]


def _split_sentences(text: str) -> list[str]:
    """按句号、问号、感叹号、换行分割，过滤太短的句子。"""
    raw = re.split(r"[。！？\n]+", text)
    return [s.strip() for s in raw if len(s.strip()) >= 15]


def _make_fill_blank(sentence: str, qid: int) -> dict | None:
    """从单句生成一道填空题。找到关键内容挖空。"""
    # 1. 找英文术语
    m = re.search(r"[A-Za-z]{3,}", sentence)
    if m:
        key = m.group()
        return _build_question(qid, sentence, key, m.start(), m.end())

    # 2. 找数字
    m = re.search(r"\d{1,6}(?:\.\d+)?", sentence)
    if m:
        key = m.group()
        return _build_question(qid, sentence, key, m.start(), m.end())

    # 3. 找括号内容
    m = re.search(r"[（(][^）)]+[）)]", sentence)
    if m:
        key = m.group()
        return _build_question(qid, sentence, key, m.start(), m.end())

    # 4. 找引号内容
    m = re.search(r"“[^”]+”", sentence)  # left/right double quotes
    if not m:
        m = re.search(r"「[^」]+」", sentence)  # CJK corner brackets
    if m:
        key = m.group()
        return _build_question(qid, sentence, key, m.start(), m.end())

    # 5. 兜底：取句子中段 2-6 个汉字
    chars = list(sentence)
    if len(chars) >= 10:
        start = len(chars) // 2 - 2
        end = start + min(4, len(chars) - start - 2)
        if start >= 2 and end < len(chars) - 1:
            key = "".join(chars[start:end])
            byte_start = len("".join(chars[:start]).encode("utf-8"))  # approximate, fine for this use
            return _build_question(qid, sentence, key, start, end)

    return None


def _build_question(qid: int, sentence: str, key: str, start: int, end: int) -> dict:
    """构建一道填空题。"""
    question = sentence[:start] + "______" + sentence[end:]
    return {
        "id": qid,
        "type": "填空题",
        "difficulty": "中等",
        "question": question.strip(),
        "answer": key,
        "explanation": "",
    }

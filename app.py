"""智能出题系统 — Streamlit UI。

流程: 上传文档 → 生成题目 → 在线答题 → 自动判分
"""

import json
import random

import pandas as pd
import streamlit as st

import database as db
from document_parser import MAX_CHARS, parse_document
from question_generator import generate_questions
from text_extractor import extract_sequential

# ─── 页面配置 ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="智能出题系统",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── 会话状态初始化 ─────────────────────────────────────────────────
DEFAULTS = {
    "stage": "upload",       # upload | generate | quiz | result
    "content": "",           # 解析后的文档文本
    "doc_name": "",          # 文档文件名
    "truncated": False,      # 文档是否被截断
    "questions": [],         # 生成的题目列表
    "types_used": [],        # 题型分布
    "quiz_id": None,         # 数据库中的试卷 ID
    "user_answers": {},      # 用户答案 {q_id: answer}
    "grading_result": {},    # 评分结果 {q_id: {"correct": bool, "user": ..., "expected": ...}}
    "score": 0,
    "total_scored": 0,
}
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val


def reset_to_upload():
    """重置所有状态，回到上传页。"""
    for key, val in DEFAULTS.items():
        st.session_state[key] = val


# ─── 题型判断 & 评分辅助函数 ─────────────────────────────────────
def _is_choice(qtype: str, q: dict) -> bool:
    return "选择" in qtype and "options" in q


def _is_truefalse(qtype: str) -> bool:
    return "判断" in qtype or "是非" in qtype


def _is_fillin(qtype: str) -> bool:
    return "填空" in qtype


def _is_shortanswer(qtype: str) -> bool:
    return any(kw in qtype for kw in ("简答", "论述", "问答", "问答题"))


def _is_coding(qtype: str) -> bool:
    return "编程" in qtype


def _is_matching(qtype: str, q: dict) -> bool:
    return "配对" in qtype and "pairs" in q


def _grade_exact(user_ans, expected) -> bool:
    """精确匹配（处理判断题布尔值/中文 和 选择题字母）。"""
    user = str(user_ans).strip().upper()
    exp = str(expected).strip()

    # 判断题：容错中文 "正确"/"错误" 和布尔值 true/false
    true_patterns = {"TRUE", "正确", "对", "YES", "是"}
    false_patterns = {"FALSE", "错误", "错", "NO", "否"}
    if user in true_patterns:
        if exp.lower() == "true" or exp in true_patterns:
            return True
        return False
    if user in false_patterns:
        if exp.lower() == "false" or exp in false_patterns:
            return True
        return False

    # 选择题：从 expected 提取选项字母（兼容 "B" / "B. xxx" / "答案是B" 等）
    exp_letter = ""
    for ch in exp:
        if ch.upper() in "ABCDEF":
            exp_letter = ch.upper()
            break
    if exp_letter and user == exp_letter:
        return True

    # 兜底：精确匹配
    return user == exp.upper()


def _grade_fuzzy(user_ans: str, expected: str) -> bool:
    """模糊匹配：去空格、大小写不敏感。"""
    user = user_ans.strip().replace(" ", "").replace("　", "").lower()
    exp = expected.strip().replace(" ", "").replace("　", "").lower()
    return user == exp or exp in user or user in exp


def _grade_matching(user_ans: str | dict, expected: dict) -> bool:
    """配对题评分：逐项精确匹配，正确率 >= 80% 算对。"""
    try:
        if isinstance(user_ans, str):
            user_dict = json.loads(user_ans)
        else:
            user_dict = user_ans
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(expected, dict) or not isinstance(user_dict, dict):
        return False
    total = len(expected)
    correct = sum(1 for k in expected if k in user_dict and str(user_dict[k]) == str(expected[k]))
    return correct / total >= 0.8 if total > 0 else False


def _format_answer_for_display(answer, q: dict | None = None) -> str:
    """格式化答案用于展示，区分"答案内容"和"判分结论"。"""
    if answer is None:
        return "（未作答）"
    # 布尔值
    if isinstance(answer, bool):
        return "[正确]" if answer else "[错误]"
    # 字符串形式的判断题答案
    s = str(answer).strip()
    if s in ("正确", "对", "true", "True", "TRUE"):
        return "[正确]"
    if s in ("错误", "错", "false", "False", "FALSE"):
        return "[错误]"
    # 单个选项字母
    if len(s) == 1 and s.upper() in "ABCDEF":
        return s.upper()
    # dict（配对题答案等）
    if isinstance(answer, dict):
        if q and set(answer.keys()) <= {"A", "B", "C", "D", "E", "F"}:
            return str(answer)
        return ", ".join(f"{k} -> {v}" for k, v in answer.items())
    return s[:200]


# ─── 预设模型配置 ────────────────────────────────────────────────
PROVIDERS = {
    "Anthropic Claude": {
        "base_url": None,
        "model": "claude-sonnet-4-6",
        "api_type": "anthropic",
        "key_hint": "sk-ant-...",
        "desc": "Anthropic 官方，中英文理解力强",
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_type": "openai",
        "key_hint": "sk-...",
        "desc": "国产高性价比，中文能力强",
    },
    "OpenAI GPT": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "api_type": "openai",
        "key_hint": "sk-...",
        "desc": "OpenAI 官方 GPT-4o",
    },
    "通义千问 (阿里云)": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_type": "openai",
        "key_hint": "sk-...",
        "desc": "阿里云通义千问",
    },
    "智谱 GLM": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "api_type": "openai",
        "key_hint": "xxx.",
        "desc": "智谱 AI，GLM-4 系列",
    },
    "Moonshot (月之暗面)": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "api_type": "openai",
        "key_hint": "sk-...",
        "desc": "Kimi 同厂，长文本处理好",
    },
    "自定义": {
        "base_url": "",
        "model": "",
        "api_type": "openai",
        "key_hint": "",
        "desc": "手动填写 Base URL 和模型名",
    },
}


# ─── 侧边栏 ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("[API] 配置")

    provider_name = st.selectbox(
        "模型服务商",
        list(PROVIDERS.keys()),
        format_func=lambda n: f"{n}  ",
        help="选择你要使用的 LLM 服务商",
    )
    provider = PROVIDERS[provider_name]

    # 显示当前选择的模型信息
    if provider_name != "自定义":
        st.caption(f"模型：{provider['model']}")
        st.caption(f"{provider['desc']}")

    api_key = st.text_input(
        "API Key",
        type="password",
        placeholder=provider["key_hint"] or "输入 API Key",
    )

    # 自定义模式：手动填写 base_url 和 model
    if provider_name == "自定义":
        api_base = st.text_input(
            "Base URL",
            placeholder="https://api.example.com/v1",
            help="OpenAI 兼容的 API 地址",
        )
        model = st.text_input(
            "模型名称",
            placeholder="model-name",
        )
    else:
        api_base = provider["base_url"]
        model = provider["model"]

    st.divider()

    st.header("[设置] 出题参数")

    difficulty = st.select_slider(
        "难度等级",
        options=["简单", "中等", "困难"],
        value="中等",
    )
    mode = st.radio(
        "出题模式",
        ["随机出题", "按顺序出题"],
        horizontal=True,
    )
    no_llm = st.checkbox("不调用大模型（直接从文档提取填空，无解析）", value=False)

    # 顺序不调 LLM → 全部提取；随机不调 LLM → 需要数量；调 LLM → 需要数量
    hide_counts = no_llm and mode == "按顺序出题"
    if not hide_counts:
        st.caption("各题型数量（设为 0 则不出该题型）")
        col1, col2 = st.columns(2)
        with col1:
            choice_count = st.number_input("[选择] 选择题", min_value=0, max_value=30, value=10)
            tf_count = st.number_input("[判断] 判断题", min_value=0, max_value=30, value=5)
            fill_count = st.number_input("[填空] 填空题", min_value=0, max_value=30, value=5)
        with col2:
            short_count = st.number_input("[简答] 简答题", min_value=0, max_value=20, value=5)
            code_count = st.number_input("[编程] 编程题", min_value=0, max_value=10, value=0)
    else:
        choice_count = tf_count = fill_count = short_count = code_count = 0
        st.caption("将提取文档中所有可用题目")
    preference = st.text_input(
        "额外偏好（可选）",
        placeholder="如：多出计算题、侧重第三章...",
    )

    total_count = choice_count + tf_count + fill_count + short_count + code_count

    st.divider()

    hide_counts = no_llm and mode == "按顺序出题"
    has_count = total_count > 0 or hide_counts  # 顺序不调LLM 不需要设数量
    can_generate = (bool(api_key) or no_llm) and has_count and st.session_state.stage in ("upload", "generate", "quiz", "result")
    if st.button(">> 生成题目", type="primary", disabled=not can_generate, use_container_width=True):
        if not st.session_state.content:
            st.error("请先上传文档")
        else:
            st.session_state.stage = "generate"
            st.rerun()
    if total_count == 0 and not no_llm and api_key:
        st.caption("!! 请至少设置一种题型的数量")

    # 历史试卷
    quizzes = db.get_recent_quizzes(5)
    if quizzes:
        st.divider()
        st.caption("[历史] 最近试卷")
        for q in quizzes:
            label = f"{q['document_name'][:20]} — {q['total_questions']}题 ({q['difficulty']})"
            if st.button(label, key=f"load_{q['id']}", use_container_width=True):
                quiz = db.get_quiz(q["id"])
                if quiz:
                    st.session_state.questions = quiz["questions_json"]
                    st.session_state.types_used = list({q["type"] for q in quiz["questions_json"]})
                    st.session_state.quiz_id = q["id"]
                    st.session_state.stage = "quiz"
                    st.session_state.user_answers = {}
                    st.session_state.grading_result = {}
                    st.session_state.content = ""  # 历史试卷无原始文档
                    st.session_state.doc_name = quiz["document_name"]
                    st.rerun()


# ─── 主区域 ────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["[出题答题]", "[错题本]", "[统计]"])

# ═══════════════════════════════════════════════════════════════════
# Tab 1: 出题答题
# ═══════════════════════════════════════════════════════════════════
with tab1:
    stage = st.session_state.stage

    # ── 阶段 1: 上传文档 ──────────────────────────────────────────
    if stage == "upload":
        st.markdown("### 上传文档")
        st.markdown("支持 Word (.docx)、PDF (.pdf)、纯文本 (.txt, .md)，可多选")

        uploaded = st.file_uploader(
            "选择文件",
            type=["docx", "pdf", "txt", "md"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )

        if uploaded:
            all_text = []
            all_names = []
            parse_errors = []

            with st.spinner("正在解析文档..."):
                for file in uploaded:
                    try:
                        text, truncated, maybe_scanned = parse_document(file.read(), file.name)
                        if maybe_scanned:
                            st.warning(f"[!] `{file.name}` 可能是扫描件（无文字层），已跳过")
                            continue
                        all_text.append(text)
                        all_names.append(file.name)
                        if truncated:
                            st.warning(f"[!] `{file.name}` 过长，已截取前 {MAX_CHARS} 字符")
                    except ValueError as e:
                        parse_errors.append(f"`{file.name}`: {e}")

            if parse_errors:
                for err in parse_errors:
                    st.warning(f"[!] 跳过：{err}")

            if all_text:
                st.session_state.content = "\n\n---\n\n".join(all_text)
                st.session_state.doc_name = " + ".join(all_names)
                st.session_state.truncated = any(
                    len(t) >= MAX_CHARS for t in all_text
                )

            st.success(f"[OK] 解析成功：{len(uploaded)} 个文档，共 {len(st.session_state.content)} 字符")
            if st.session_state.truncated:
                st.warning(f"[!] 部分文档过长，已截取前 {MAX_CHARS} 字符用于出题")

            with st.expander("[预览] 查看提取的文本"):
                st.text_area("文档内容", st.session_state.content, height=250, disabled=True)

            st.info("在左侧边栏设置题目参数，然后点击「生成题目」")

    # ── 阶段 2: 生成中 / 显示题目 ─────────────────────────────────
    elif stage == "generate":
        st.markdown("### [生成] 正在生成题目...")
        st.info(f"AI 正在分析 `{st.session_state.doc_name}` 的内容，自主判断题型并出题...")

        with st.spinner("AI 思考中，可能需要 15-30 秒..."):
            try:
                config = {
                    "choice": choice_count,
                    "true_false": tf_count,
                    "fill_in_blank": fill_count,
                    "short_answer": short_count,
                    "coding": code_count,
                    "difficulty": difficulty,
                    "preference": preference or "",
                    "sequential": (mode == "按顺序出题"),
                    "no_llm": no_llm,
                }
                if no_llm:
                    # 不调 LLM，直接从文档提取
                    questions = extract_sequential(st.session_state.content, config)
                    if config.get("sequential"):
                        # 按顺序全部显示
                        pass
                    else:
                        # 随机模式：打乱后按数量截取
                        random.shuffle(questions)
                        total = choice_count + tf_count + fill_count + short_count + code_count
                        if total > 0:
                            questions = questions[:total]
                        for i, q in enumerate(questions, 1):
                            q["id"] = i
                    result = {"question_types_used": ["填空题"], "questions": questions}
                else:
                    result = generate_questions(
                        st.session_state.content,
                        config,
                        api_key,
                        base_url=api_base or None,
                        model=model or "claude-sonnet-4-6",
                        api_type=provider["api_type"],
                    )
                st.session_state.questions = result.get("questions", [])
                st.session_state.types_used = result.get("question_types_used", [])

                # 清洗 answer 字段（不同 LLM 格式不统一）
                for q in st.session_state.questions:
                    qtype = q.get("type", "")
                    if _is_choice(qtype, q) and "options" in q:
                        ans = str(q["answer"]).strip()
                        opts = q["options"]
                        if ans not in opts:
                            # 尝试提取首字母
                            first_letter = ans[0].upper() if ans else ""
                            if first_letter in opts:
                                q["answer"] = first_letter
                            # 尝试在选项内容中匹配
                            else:
                                for k, v in opts.items():
                                    if ans in v or v in ans:
                                        q["answer"] = k
                                        break
                st.session_state.user_answers = {}
                st.session_state.grading_result = {}

                # 存入数据库
                qid = db.save_quiz(
                    st.session_state.doc_name,
                    len(st.session_state.questions),
                    difficulty,
                    st.session_state.questions,
                )
                st.session_state.quiz_id = qid
                st.session_state.stage = "quiz"
                st.rerun()

            except Exception as e:
                st.error(f"生成失败：{e}")
                if st.button("<< 返回重试"):
                    st.session_state.stage = "upload"
                    st.rerun()

    # ── 阶段 3: 答题 ──────────────────────────────────────────────
    elif stage == "quiz" and st.session_state.questions:
        questions = st.session_state.questions

        # 概览
        st.markdown("### 答题卡")
        type_counts = {}
        for q in questions:
            t = q.get("type", "未知")
            type_counts[t] = type_counts.get(t, 0) + 1
        cols = st.columns(len(type_counts) + 1)
        cols[0].metric("[总] 总题数", len(questions))
        for i, (t, c) in enumerate(type_counts.items(), 1):
            cols[i].metric(t, c)

        st.divider()

        # 记录每个题目的 widget key 映射，提交后从 session_state 读取
        widget_map = {}   # qid_str -> ("type", key_or_keys)

        with st.form("quiz_form"):
            for q in questions:
                qid = q["id"]
                qtype = q.get("type", "")
                diff = q.get("difficulty", "")
                diff_icon = {"简单": "[E]", "中等": "[M]", "困难": "[H]"}.get(diff, "")

                st.markdown(f"**{qid}. [{diff_icon} {diff}] [{qtype}]** {q['question']}")

                if _is_choice(qtype, q):
                    options = q.get("options", {})
                    option_keys = list(options.keys())
                    key = f"radio_{qid}"
                    st.radio(
                        f"q_{qid}",
                        option_keys,
                        format_func=lambda k, opts=options: f"{k}. {opts.get(k, '')}",
                        key=key,
                        horizontal=True,
                        label_visibility="collapsed",
                        index=None,
                    )
                    widget_map[str(qid)] = ("choice", key)

                elif _is_truefalse(qtype):
                    key = f"tf_{qid}"
                    st.radio(
                        f"q_{qid}",
                        ["正确", "错误"],
                        key=key,
                        horizontal=True,
                        label_visibility="collapsed",
                        index=None,
                    )
                    widget_map[str(qid)] = ("truefalse", key)

                elif _is_fillin(qtype):
                    key = f"fill_{qid}"
                    st.text_input(
                        f"q_{qid}",
                        key=key,
                        label_visibility="collapsed",
                        placeholder="请输入你的答案",
                    )
                    widget_map[str(qid)] = ("fillin", key)

                elif _is_shortanswer(qtype):
                    key = f"sa_{qid}"
                    st.text_area(
                        f"q_{qid}",
                        key=key,
                        label_visibility="collapsed",
                        placeholder="请输入你的回答",
                        height=100,
                    )
                    widget_map[str(qid)] = ("shortanswer", key)

                elif _is_matching(qtype, q):
                    pairs = q.get("pairs", {})
                    keys = list(pairs.keys())
                    st.caption("请为每项选择配对：")
                    match_keys = []
                    for k in keys:
                        mk = f"match_{qid}_{k}"
                        st.selectbox(
                            f"{k} ->",
                            [""] + keys,
                            key=mk,
                            label_visibility="collapsed",
                        )
                        match_keys.append((k, mk))
                    widget_map[str(qid)] = ("matching", match_keys)

                elif _is_coding(qtype):
                    key = f"code_{qid}"
                    st.caption("（自主批改 — 编程题）")
                    sample_input = q.get("sample_input", "")
                    sample_output = q.get("sample_output", "")
                    if sample_input or sample_output:
                        st.caption(f"In: {sample_input}")
                        st.caption(f"Out: {sample_output}")
                    st.text_area(
                        f"q_{qid}",
                        key=key,
                        label_visibility="collapsed",
                        placeholder="# 在此输入你的代码",
                        height=150,
                    )
                    widget_map[str(qid)] = ("unknown", key)

                else:
                    key = f"unknown_{qid}"
                    st.caption("（自主批改题型）")
                    st.text_area(
                        f"q_{qid}",
                        key=key,
                        label_visibility="collapsed",
                        placeholder="请输入你的回答（此题需自行对照答案）",
                        height=100,
                    )
                    widget_map[str(qid)] = ("unknown", key)

                st.markdown("")

            submitted = st.form_submit_button("[提交] 提交答案", type="primary", use_container_width=True)

            if submitted:
                # 从 session_state 读取用户答案
                user_answers = {}
                for qid_str, (wtype, wkey) in widget_map.items():
                    if wtype == "matching":
                        match_result = {}
                        for k, mk in wkey:
                            val = st.session_state.get(mk, "")
                            if val:
                                match_result[k] = val
                        user_answers[qid_str] = json.dumps(match_result) if match_result else ""
                    else:
                        val = st.session_state.get(wkey)
                        user_answers[qid_str] = val if val is not None else ""

                st.session_state.user_answers = user_answers

                # 评分
                score = 0
                total_scored = 0
                grading = {}

                for q in questions:
                    qid = str(q["id"])
                    qtype = q.get("type", "")
                    user_ans = user_answers.get(qid)
                    expected = q.get("answer")

                    # 判断是否未作答
                    unanswered = user_ans is None or user_ans == "" or user_ans == "{}"
                    if unanswered:
                        grading[qid] = {"correct": None, "user": user_ans, "expected": expected, "verdict": "未作答"}
                        continue

                    if _is_choice(qtype, q) or _is_truefalse(qtype):
                        correct = _grade_exact(user_ans, expected)
                        total_scored += 1
                    elif _is_fillin(qtype):
                        correct = _grade_fuzzy(user_ans, str(expected))
                        total_scored += 1
                    elif _is_matching(qtype, q):
                        correct = _grade_matching(user_ans, expected)
                        total_scored += 1
                    elif _is_shortanswer(qtype):
                        correct = None
                    else:
                        correct = None

                    if correct:
                        score += 1

                    grading[qid] = {"correct": correct, "user": user_ans, "expected": expected}

                st.session_state.grading_result = grading
                st.session_state.score = score
                st.session_state.total_scored = total_scored

                # 存答题记录和错题
                db.save_attempt(st.session_state.quiz_id, user_answers, score, total_scored)
                wrong_list = []
                for qid, g in grading.items():
                    if g["correct"] is False:
                        q = next((x for x in questions if str(x["id"]) == qid), None)
                        if q:
                            wrong_list.append({"question": q, "user_answer": g["user"]})
                if wrong_list:
                    db.add_wrong_questions(st.session_state.quiz_id, wrong_list)

                st.session_state.stage = "result"
                st.rerun()

    # ── 阶段 4: 评分结果 ──────────────────────────────────────────
    elif stage == "result" and st.session_state.grading_result:
        questions = st.session_state.questions
        grading = st.session_state.grading_result
        score = st.session_state.score
        total_scored = st.session_state.total_scored

        st.markdown("### 评分结果")

        # 得分卡片
        auto_total = sum(1 for g in grading.values() if g["correct"] is not None)
        if total_scored > 0:
            percentage = round(score / total_scored * 100, 1)
            st.markdown(
                f"**自动评分：{score}/{total_scored} 题正确，得分率 {percentage}%**"
                + (f"（另有 {len(questions) - auto_total} 题需手动检查）" if len(questions) > auto_total else "")
            )
            st.progress(score / total_scored)
        else:
            st.info("本次均为非自动评分题型，请自行对照答案批改。")

        st.divider()

        for q in questions:
            qid = str(q["id"])
            g = grading.get(qid)
            if g is None:
                continue

            verdict = g["correct"]
            qtype = q.get("type", "")

            # ── 根据判分结果选择醒目容器 ──
            if verdict is True:
                container = st.container(border=True)
                status_label = ":green[*** 答对了 ***]"
            elif verdict is False:
                container = st.container(border=True)
                status_label = ":red[*** 答错了 ***]"
            else:
                container = st.container(border=True)
                status_label = ":orange[*** 需手动批改 ***]"

            with container:
                st.markdown(f"**{q['id']}. {status_label} [{qtype}]** {q.get('question', '')}")

                # ── 选择题：展示完整选项 ──
                if _is_choice(qtype, q):
                    options = q.get("options", {})
                    expected_letter = g["expected"]
                    exp_letter = ""
                    if isinstance(expected_letter, str):
                        for ch in str(expected_letter):
                            if ch.upper() in "ABCDEF":
                                exp_letter = ch.upper()
                                break
                    if not exp_letter and isinstance(expected_letter, str):
                        exp_letter = expected_letter.strip().upper()

                    user_letter = str(g["user"]).strip().upper() if g["user"] else ""

                    for opt_key, opt_val in options.items():
                        if opt_key == exp_letter and opt_key == user_letter and verdict:
                            st.markdown(f":green[{opt_key}. {opt_val}]  << 正确答案（你答对了）")
                        elif opt_key == exp_letter:
                            st.markdown(f":green[{opt_key}. {opt_val}]  << 正确答案")
                        elif opt_key == user_letter and not verdict:
                            st.markdown(f":red[{opt_key}. {opt_val}]  << 你的答案")
                        else:
                            st.markdown(f"{opt_key}. {opt_val}")
                else:
                    # ── 非选择题：显示答案 ──
                    user_display = _format_answer_for_display(g["user"])
                    st.markdown(f"你的答案：{user_display}")

                    if verdict is False:
                        expected_display = _format_answer_for_display(g["expected"])
                        st.markdown(f":green[正确答案：**{expected_display}**]")

                    if verdict is None:
                        st.markdown(f"参考答案：{_format_answer_for_display(g['expected'])}")
                        st.caption("（此题需自行对比判断）")

                # 解析
                explanation = q.get("explanation", "")
                if explanation:
                    expanded = verdict is False or verdict is None
                    with st.expander("[解析] 答案解析", expanded=expanded):
                        st.markdown(explanation)

            st.markdown("")

        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button(">> 重新出题", use_container_width=True):
                if st.session_state.content:
                    st.session_state.stage = "generate"
                else:
                    reset_to_upload()
                st.rerun()
        with col_b:
            if st.button(">> 上传新文档", use_container_width=True):
                reset_to_upload()
                st.rerun()


# ═══════════════════════════════════════════════════════════════════
# Tab 2: 错题本
# ═══════════════════════════════════════════════════════════════════
with tab2:
    st.markdown("### [错题] 错题本")

    wrongs = db.get_wrong_questions(100)
    if not wrongs:
        st.info("还没有错题，继续加油！")
    else:
        st.markdown(f"共 **{db.get_wrong_count()}** 道错题")

        # 筛选
        filter_type = st.selectbox(
            "按题型筛选",
            ["全部"] + list({w["question_json"].get("type", "未知") for w in wrongs if isinstance(w["question_json"], dict)}),
            key="wrong_filter",
        )
        filtered = wrongs
        if filter_type != "全部":
            filtered = [w for w in wrongs if isinstance(w["question_json"], dict) and w["question_json"].get("type") == filter_type]

        for w in filtered[:50]:
            q = w["question_json"]
            if not isinstance(q, dict):
                continue

            st.markdown(f"**[{q.get('type', '')}]** {q.get('question', '')}")

            cols = st.columns([1, 1, 1, 2])
            cols[0].caption(f"[X] 你答：{w['user_answer'][:50]}")
            cols[1].caption(f"[V] 正确：{str(q.get('answer', ''))[:50]}")
            cols[2].caption(f"错过 {w['wrong_count']} 次")
            cols[3].caption(f"来源：{w.get('document_name', '未知')} · {w['last_wrong_at'][:10]}")

            explanation = q.get("explanation", "")
            if explanation:
                with st.expander("[解析] 解析"):
                    st.markdown(explanation)
            st.divider()

        # 操作按钮
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("[清空] 清空错题本", use_container_width=True, type="secondary"):
                db.clear_wrong_questions()
                st.rerun()
        with col2:
            if st.button("[重做] 重做错题", use_container_width=True):
                if wrongs:
                    # 取最近的错题，最多 30 道
                    redo_questions = [w["question_json"] for w in wrongs[:30] if isinstance(w["question_json"], dict)]
                    # 重新编号
                    for i, q in enumerate(redo_questions, 1):
                        q["id"] = i
                    st.session_state.questions = redo_questions
                    st.session_state.types_used = list({q.get("type", "") for q in redo_questions})
                    st.session_state.user_answers = {}
                    st.session_state.grading_result = {}
                    st.session_state.stage = "quiz"
                    st.session_state.content = ""  # 无原始文档
                    st.session_state.doc_name = "错题重做"
                    # 创建新 quiz 记录
                    redo_quiz_id = db.save_quiz("错题重做", len(redo_questions), "", redo_questions)
                    st.session_state.quiz_id = redo_quiz_id
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════
# Tab 3: 统计
# ═══════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### [统计] 学习统计")

    stats = db.get_stats()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("生成试卷数", stats["total_quizzes"])
    col2.metric("答题次数", stats["total_attempts"])
    col3.metric("错题数", stats["total_wrong"])
    col4.metric(
        "最近平均正确率",
        f"{stats['average_score']}%" if stats["total_attempts"] > 0 else "-",
    )

    # 最近答题趋势
    if stats["recent_scores"]:
        st.divider()
        st.markdown("#### 最近答题正确率趋势")
        recent_data = []
        for i, (s, t) in enumerate(zip(stats["recent_scores"], stats["recent_totals"])):
            pct = round(s / t * 100, 1) if t > 0 else 0
            recent_data.append({"次数": f"第{i+1}次", "正确率": pct})

        if recent_data:
            df = pd.DataFrame(recent_data)
            st.bar_chart(df.set_index("次数"), use_container_width=True)

    if stats["total_attempts"] == 0:
        st.info("还没有答题记录，去生成一套试卷吧！")

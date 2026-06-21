"""SQLite 数据持久化模块 — 题库、答题记录、错题本。"""

import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quiz.db")


@contextmanager
def get_connection():
    """获取数据库连接（上下文管理器），自动提交和关闭。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _create_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_name TEXT,
            total_questions INTEGER,
            difficulty TEXT,
            questions_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER,
            user_answers_json TEXT,
            score INTEGER,
            total_scored INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wrong_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER,
            question_json TEXT,
            user_answer TEXT,
            wrong_count INTEGER DEFAULT 1,
            last_wrong_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)


# ─── 试卷相关 ───────────────────────────────────────────────────

def save_quiz(document_name: str, total: int, difficulty: str, questions: list) -> int:
    """保存生成的试卷，返回 quiz_id。"""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO quizzes (document_name, total_questions, difficulty, questions_json) VALUES (?, ?, ?, ?)",
            (document_name, total, difficulty, json.dumps(questions, ensure_ascii=False)),
        )
        return cursor.lastrowid


def get_quiz(quiz_id: int) -> dict | None:
    """获取指定试卷。"""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["questions_json"] = json.loads(d["questions_json"])
    return d


def get_recent_quizzes(limit: int = 10) -> list[dict]:
    """获取最近的试卷列表。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, document_name, total_questions, difficulty, created_at FROM quizzes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── 答题记录 ───────────────────────────────────────────────────

def save_attempt(quiz_id: int, user_answers: dict, score: int, total_scored: int) -> int:
    """保存答题记录，返回 attempt_id。"""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO attempts (quiz_id, user_answers_json, score, total_scored) VALUES (?, ?, ?, ?)",
            (quiz_id, json.dumps(user_answers, ensure_ascii=False), score, total_scored),
        )
        return cursor.lastrowid


def get_all_attempts(limit: int = 50) -> list[dict]:
    """获取最近的答题记录。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT a.*, q.document_name, q.difficulty FROM attempts a LEFT JOIN quizzes q ON a.quiz_id = q.id ORDER BY a.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── 错题本 ─────────────────────────────────────────────────────

def add_wrong_questions(quiz_id: int, wrong_list: list[dict]):
    """批量添加错题。相同题目增加 wrong_count 而非重复插入。"""
    if not wrong_list:
        return

    with get_connection() as conn:
        # 先查出现有错题的 question_json → id 映射
        existing_map = {}
        for item in wrong_list:
            qj = json.dumps(item["question"], ensure_ascii=False) if isinstance(item["question"], dict) else item["question"]
            existing_map[qj] = None  # 占位

        rows = conn.execute(
            "SELECT id, question_json, wrong_count FROM wrong_questions WHERE question_json IN ({})".format(
                ",".join("?" * len(existing_map))
            ),
            list(existing_map.keys()),
        ).fetchall()
        for r in rows:
            existing_map[r["question_json"]] = (r["id"], r["wrong_count"])

        # 更新已存在的
        updates = []
        inserts = []
        for item in wrong_list:
            qj = json.dumps(item["question"], ensure_ascii=False) if isinstance(item["question"], dict) else item["question"]
            ua = str(item["user_answer"])
            if existing_map.get(qj):
                updates.append((ua, existing_map[qj][0]))
            else:
                inserts.append((quiz_id, qj, ua))

        if updates:
            conn.executemany(
                "UPDATE wrong_questions SET wrong_count = wrong_count + 1, user_answer = ?, last_wrong_at = CURRENT_TIMESTAMP WHERE id = ?",
                updates,
            )
        if inserts:
            conn.executemany(
                "INSERT INTO wrong_questions (quiz_id, question_json, user_answer) VALUES (?, ?, ?)",
                inserts,
            )


def get_wrong_questions(limit: int = 50) -> list[dict]:
    """获取错题本列表。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT wq.*, q.document_name FROM wrong_questions wq LEFT JOIN quizzes q ON wq.quiz_id = q.id ORDER BY wq.last_wrong_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["question_json"] = json.loads(d["question_json"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(d)
    return result


def get_wrong_count() -> int:
    """返回错题总数。"""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM wrong_questions").fetchone()
    return row["cnt"]


def clear_wrong_questions():
    """清空错题本。"""
    with get_connection() as conn:
        conn.execute("DELETE FROM wrong_questions")


# ─── 统计 ────────────────────────────────────────────────────────

def get_stats() -> dict:
    """返回统计概览（单次查询）。"""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM quizzes) as total_quizzes,
                (SELECT COUNT(*) FROM attempts) as total_attempts,
                (SELECT COUNT(*) FROM wrong_questions) as total_wrong
        """).fetchone()

        score_rows = conn.execute(
            "SELECT score, total_scored FROM attempts ORDER BY created_at DESC LIMIT 50"
        ).fetchall()

    scores = [r["score"] for r in score_rows if r["total_scored"] > 0]
    totals = [r["total_scored"] for r in score_rows if r["total_scored"] > 0]
    avg_score = sum(scores) / len(scores) * 100 if scores else 0

    return {
        "total_quizzes": row["total_quizzes"],
        "total_attempts": row["total_attempts"],
        "total_wrong": row["total_wrong"],
        "average_score": round(avg_score, 1),
        "recent_scores": scores,
        "recent_totals": totals,
    }

"""
episodic_store.py
=================
Episodic Memory Layer for BCSBatighor GK Knowledge Graph System.

Role: Saif — Episodic Memory Layer Lead

This module implements the Episodic Memory Layer as defined in
episodic_memory_design.md. It stores system interaction events
(episodes) and references KG fact IDs from kg_builder.py without
duplicating any fact text.

Database: memory.db (SQLite)
Tables:
    - episodes          : High-level event metadata
    - episode_facts     : Many-to-many link: episode ↔ KG fact ID
    - episode_mcqs      : Generated MCQs per episode
    - rejection_logs    : Failure diagnostics per episode
"""

import sqlite3
import uuid
import json
import math
import datetime
import os
from typing import List, Optional, Dict, Any

from bcs.logging_config import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = "runtime/memory.db"
DECAY_LAMBDA = 0.05
PRESERVE_SCORE_THRESHOLD = 0.85
PRESERVE_ACCEPTED = 1


# ---------------------------------------------------------------------------
# EpisodicMemory Class
# ---------------------------------------------------------------------------

class EpisodicMemory:
    """
    Episodic Memory Layer for the BCSBatighor GK system.

    Provides long-term experiential memory by recording how the system
    used KG facts, what MCQs it generated, how they were evaluated, and
    how the system improved over time.

    All fact references use KG fact IDs (e.g., 'FACT_a1b2c3d4') from
    kg_builder.py — no fact text is duplicated here.

    Usage
    -----
    mem = EpisodicMemory("memory.db")

    episode_id = mem.write_episode(
        input_question="বাংলাদেশের রাজধানী কী?",
        intent="factual_recall",
        blueprint="single_correct_answer",
        topic="Geography",
        fact_ids=["FACT_abc123", "FACT_def456"],
        mcqs=[{...}],
        overall_score=0.92,
        accepted=1,
        generation_config={"difficulty": "easy", "prompt_version": "v1"}
    )
    """

    # ------------------------------------------------------------------
    # Init & Schema Setup
    # ------------------------------------------------------------------

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        """
        Connect to (or create) the SQLite memory database and
        initialize all four tables.

        Parameters
        ----------
        db_path : str
            Path to the SQLite database file. Created if it does not exist.
        """
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()
        self._migrate_schema()
        log.debug("Connected to database: %s", os.path.abspath(db_path))

    def _create_schema(self):
        cur = self._conn.cursor()

        # ---- Table 1: episodes ----------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id          TEXT PRIMARY KEY,
                input_question      TEXT NOT NULL,
                intent              TEXT,
                blueprint           TEXT,
                topic               TEXT,
                timestamp           TEXT NOT NULL,
                generation_config   TEXT,
                embedding           BLOB,
                overall_score       REAL DEFAULT 0.0,
                accepted            INTEGER DEFAULT 0,
                decay_score         REAL DEFAULT 1.0
            );
        """)

        # ---- Table 2: episode_facts ------------------------------------
        # Links an episode to one or more KG Fact IDs (no fact text stored)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS episode_facts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id  TEXT NOT NULL,
                fact_id     TEXT NOT NULL,
                FOREIGN KEY (episode_id) REFERENCES episodes(episode_id)
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ef_episode
            ON episode_facts(episode_id);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ef_fact
            ON episode_facts(fact_id);
        """)

        # ---- Table 3: episode_mcqs -------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS episode_mcqs (
                mcq_id              TEXT PRIMARY KEY,
                episode_id          TEXT NOT NULL,
                question            TEXT NOT NULL,
                options             TEXT,
                correct_answer      TEXT,
                difficulty          TEXT,
                quality_score       REAL DEFAULT 0.0,
                format_score        REAL DEFAULT 0.0,
                grounding_score     REAL DEFAULT 0.0,
                clarity_score       REAL DEFAULT 0.0,
                distractor_score    REAL DEFAULT 0.0,
                regeneration_round  INTEGER DEFAULT 0,
                FOREIGN KEY (episode_id) REFERENCES episodes(episode_id)
            );
        """)

        # ---- Table 4: rejection_logs -----------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rejection_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id      TEXT NOT NULL,
                reason          TEXT,
                judge_feedback  TEXT,
                timestamp       TEXT NOT NULL,
                FOREIGN KEY (episode_id) REFERENCES episodes(episode_id)
            );
        """)

        # ---- Table 5: feedback -----------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id      TEXT NOT NULL,
                mcq_id          TEXT NOT NULL,
                fact_ids        TEXT,
                rating          INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
                category        TEXT,
                comment         TEXT,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (episode_id) REFERENCES episodes(episode_id)
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_feedback_episode
            ON feedback(episode_id);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_feedback_rating
            ON feedback(rating);
        """)

        self._conn.commit()
        log.debug("Schema initialized (5 tables ready).")

    def _migrate_schema(self):
        """Add dimension score columns to episode_mcqs if missing (schema v1 → v2)."""
        cur = self._conn.cursor()
        existing = {row[1] for row in cur.execute("PRAGMA table_info(episode_mcqs)").fetchall()}
        for col in ("format_score", "grounding_score", "clarity_score", "distractor_score"):
            if col not in existing:
                cur.execute(f"ALTER TABLE episode_mcqs ADD COLUMN {col} REAL DEFAULT 0.0")
                log.info("Schema migration: added column %s to episode_mcqs", col)
        self._conn.commit()

    # ------------------------------------------------------------------
    # 5.1  write_episode()
    # ------------------------------------------------------------------

    def write_episode(
        self,
        input_question: str,
        intent: str,
        blueprint: str,
        topic: str,
        fact_ids: List[str],
        mcqs: Optional[List[Dict[str, Any]]] = None,
        overall_score: float = 0.0,
        accepted: int = 0,
        generation_config: Optional[Dict[str, Any]] = None,
        embedding: Optional[bytes] = None,
        **kwargs,   # absorbs extra keys from to_episode_payload()
                    # (avg_grounding_score, avg_distractor_score, avg_clarity_score, …)
    ) -> str:
        """
        Create a new episodic entry for a generation event.

        Parameters
        ----------
        input_question : str
            The Bangla (or English) question that triggered generation.
        intent : str
            Detected intent (e.g., 'factual_recall', 'comparison').
        blueprint : str
            MCQ blueprint type (e.g., 'single_correct_answer').
        topic : str
            Topic string matching a TOPIC node in the KG (e.g., 'Geography').
        fact_ids : list of str
            KG Fact IDs used (must match IDs from kg_builder.py).
        mcqs : list of dicts, optional
            Generated MCQ objects. Each dict should have keys:
                question, options (list), correct_answer, difficulty, quality_score,
                regeneration_round.
        overall_score : float
            Aggregate quality score for this episode (0.0–1.0).
        accepted : int
            1 if the episode was accepted by the judge, 0 otherwise.
        generation_config : dict, optional
            Snapshot of the generator configuration (difficulty, prompt_version, etc.).
        embedding : bytes, optional
            Optional vector embedding of the input question (serialized).

        Returns
        -------
        str
            The generated episode_id (UUID).
        """
        episode_id = str(uuid.uuid4())
        now_str = datetime.datetime.now().isoformat()
        config_json = json.dumps(generation_config or {}, ensure_ascii=False)

        # Compute initial decay score
        decay = self._compute_decay(age_days=0, accepted=accepted, overall_score=overall_score)

        cur = self._conn.cursor()

        # Insert into episodes
        cur.execute("""
            INSERT INTO episodes
                (episode_id, input_question, intent, blueprint, topic,
                 timestamp, generation_config, embedding,
                 overall_score, accepted, decay_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            episode_id, input_question, intent, blueprint, topic,
            now_str, config_json, embedding,
            overall_score, accepted, decay
        ))

        # Insert fact links (no fact text — only IDs)
        for fact_id in fact_ids:
            cur.execute("""
                INSERT INTO episode_facts (episode_id, fact_id)
                VALUES (?, ?)
            """, (episode_id, fact_id))

        # Insert MCQs
        for mcq in (mcqs or []):
            mcq_id = str(uuid.uuid4())
            options_json = json.dumps(mcq.get("options", []), ensure_ascii=False)
            cur.execute("""
                INSERT INTO episode_mcqs
                    (mcq_id, episode_id, question, options, correct_answer,
                     difficulty, quality_score,
                     format_score, grounding_score, clarity_score, distractor_score,
                     regeneration_round)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                mcq_id, episode_id,
                mcq.get("question", ""),
                options_json,
                mcq.get("correct_answer", ""),
                mcq.get("difficulty", "medium"),
                mcq.get("quality_score", 0.0),
                mcq.get("format_score", 0.0),
                mcq.get("grounding_score", 0.0),
                mcq.get("clarity_score", 0.0),
                mcq.get("distractor_score", 0.0),
                mcq.get("regeneration_round", 0),
            ))

        self._conn.commit()
        log.debug("Episode %s... written | topic=%s | score=%s | accepted=%s",
                  episode_id[:8], topic, overall_score, accepted)
        return episode_id

    # ------------------------------------------------------------------
    # 5.2  retrieve_similar_episodes()
    # ------------------------------------------------------------------

    def retrieve_similar_episodes(
        self,
        topic: Optional[str] = None,
        fact_ids: Optional[List[str]] = None,
        min_score: float = 0.0,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve past episodes similar to the current generation context.

        Similarity is determined by:
        - Topic match
        - Overlapping fact IDs
        - Minimum quality score threshold

        Used BEFORE a new generation round to enable experience-informed
        question generation.

        Parameters
        ----------
        topic : str, optional
            Filter by topic (exact match on the topic column).
        fact_ids : list of str, optional
            Retrieve episodes that used any of these KG fact IDs.
        min_score : float
            Only return episodes with overall_score >= min_score.
        limit : int
            Maximum number of episodes to return.

        Returns
        -------
        list of dict
            Each dict is a row from the `episodes` table with a bonus
            `matched_facts` key (list of overlapping fact IDs).
        """
        cur = self._conn.cursor()
        results = {}

        # ---- Query by topic + min_score --------------------------------
        if topic:
            cur.execute("""
                SELECT * FROM episodes
                WHERE topic = ? AND overall_score >= ?
                ORDER BY overall_score DESC
                LIMIT ?
            """, (topic, min_score, limit))
            for row in cur.fetchall():
                results[row["episode_id"]] = dict(row)
                results[row["episode_id"]]["matched_facts"] = []

        # ---- Augment with fact-based recall ----------------------------
        if fact_ids:
            placeholders = ",".join("?" * len(fact_ids))
            cur.execute(f"""
                SELECT ef.episode_id, ef.fact_id, e.*
                FROM episode_facts ef
                JOIN episodes e ON ef.episode_id = e.episode_id
                WHERE ef.fact_id IN ({placeholders})
                  AND e.overall_score >= ?
                ORDER BY e.overall_score DESC
            """, (*fact_ids, min_score))

            for row in cur.fetchall():
                eid = row["episode_id"]
                if eid not in results:
                    results[eid] = dict(row)
                    results[eid]["matched_facts"] = []
                if row["fact_id"] not in results[eid]["matched_facts"]:
                    results[eid]["matched_facts"].append(row["fact_id"])

        # ---- If no filter provided, return recent high-score episodes --
        if not topic and not fact_ids:
            cur.execute("""
                SELECT * FROM episodes
                WHERE overall_score >= ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (min_score, limit))
            for row in cur.fetchall():
                results[row["episode_id"]] = dict(row)
                results[row["episode_id"]]["matched_facts"] = []

        output = list(results.values())[:limit]
        log.debug("Found %d similar episode(s) | topic=%s | min_score=%s",
                  len(output), topic, min_score)
        return output

    # ------------------------------------------------------------------
    # 5.3  update_episode()
    # ------------------------------------------------------------------

    def update_episode(
        self,
        episode_id: str,
        accepted: Optional[int] = None,
        overall_score: Optional[float] = None,
        regeneration_round: Optional[int] = None,
        rejection_reason: Optional[str] = None,
        judge_feedback: Optional[str] = None,
    ) -> None:
        """
        Update an existing episodic entry after re-evaluation.

        Called when:
        - A judge accepts or rejects an episode post-generation
        - A regeneration round is triggered
        - A rejection log needs to be appended

        Parameters
        ----------
        episode_id : str
            Target episode UUID.
        accepted : int, optional
            New acceptance flag (0 or 1).
        overall_score : float, optional
            Updated quality score after judge evaluation.
        regeneration_round : int, optional
            Current regeneration round number.
        rejection_reason : str, optional
            Short reason code for rejection.
        judge_feedback : str, optional
            Detailed textual feedback from the judge.
        """
        cur = self._conn.cursor()
        now_str = datetime.datetime.now().isoformat()

        # Fetch current state
        cur.execute("""
            SELECT overall_score, accepted, timestamp
            FROM episodes WHERE episode_id = ?
        """, (episode_id,))
        row = cur.fetchone()
        if row is None:
            log.warning("update_episode: episode %s not found.", episode_id)
            return

        new_score = overall_score if overall_score is not None else row["overall_score"]
        new_accepted = accepted if accepted is not None else row["accepted"]

        # Recompute age-based decay
        created_at = datetime.datetime.fromisoformat(row["timestamp"])
        age_days = (datetime.datetime.now() - created_at).days
        new_decay = self._compute_decay(age_days, new_accepted, new_score)

        # Build dynamic UPDATE
        fields = ["decay_score = ?"]
        values = [new_decay]

        if accepted is not None:
            fields.append("accepted = ?")
            values.append(accepted)
        if overall_score is not None:
            fields.append("overall_score = ?")
            values.append(overall_score)

        values.append(episode_id)
        cur.execute(f"""
            UPDATE episodes SET {', '.join(fields)}
            WHERE episode_id = ?
        """, values)

        # Update regeneration_round in episode_mcqs if provided
        if regeneration_round is not None:
            cur.execute("""
                UPDATE episode_mcqs SET regeneration_round = ?
                WHERE episode_id = ?
            """, (regeneration_round, episode_id))

        # Append rejection log if reason provided
        if rejection_reason or judge_feedback:
            cur.execute("""
                INSERT INTO rejection_logs
                    (episode_id, reason, judge_feedback, timestamp)
                VALUES (?, ?, ?, ?)
            """, (episode_id, rejection_reason, judge_feedback, now_str))

        self._conn.commit()
        log.debug("Episode %s... updated | accepted=%s | score=%s | decay=%.4f",
                  episode_id[:8], new_accepted, new_score, new_decay)

    # ------------------------------------------------------------------
    # 5.4  forget_old_episodes()
    # ------------------------------------------------------------------

    def forget_old_episodes(
        self,
        decay_threshold: float = 0.1,
        max_age_days: int = 90,
        dry_run: bool = False,
    ) -> int:
        """
        Prune low-quality, old, and rejected episodes.

        Preservation rules (NOT deleted even if old):
        - accepted == 1  AND  overall_score > 0.85

        Deletion conditions (ANY one is sufficient):
        - decay_score < decay_threshold
        - age > max_age_days  AND  accepted == 0

        Parameters
        ----------
        decay_threshold : float
            Episodes with decay_score below this are candidates for deletion.
        max_age_days : int
            Episodes older than this AND not accepted are pruned.
        dry_run : bool
            If True, print what would be deleted without deleting.

        Returns
        -------
        int
            Number of episodes deleted (or that would be deleted in dry_run).
        """
        cur = self._conn.cursor()
        cutoff_date = (
            datetime.datetime.now() - datetime.timedelta(days=max_age_days)
        ).isoformat()

        # Find candidates to delete
        cur.execute("""
            SELECT episode_id, timestamp, overall_score, accepted, decay_score
            FROM episodes
            WHERE NOT (accepted = 1 AND overall_score > ?)
              AND (
                decay_score < ?
                OR (timestamp < ? AND accepted = 0)
              )
        """, (PRESERVE_SCORE_THRESHOLD, decay_threshold, cutoff_date))

        candidates = cur.fetchall()

        if dry_run:
            log.debug("[DRY RUN] %d episode(s) would be pruned.", len(candidates))
            for c in candidates:
                log.debug("  -> %s... | score=%s | accepted=%s | decay=%.4f",
                          c['episode_id'][:8], c['overall_score'], c['accepted'], c['decay_score'])
            return len(candidates)

        # Delete dependent rows first (FK cascade not guaranteed in SQLite)
        deleted_count = 0
        for c in candidates:
            eid = c["episode_id"]
            cur.execute("DELETE FROM rejection_logs WHERE episode_id = ?", (eid,))
            cur.execute("DELETE FROM episode_mcqs WHERE episode_id = ?", (eid,))
            cur.execute("DELETE FROM episode_facts WHERE episode_id = ?", (eid,))
            cur.execute("DELETE FROM episodes WHERE episode_id = ?", (eid,))
            deleted_count += 1

        self._conn.commit()
        log.debug("Pruned %d episode(s) | threshold=%s | max_age=%sd",
                  deleted_count, decay_threshold, max_age_days)
        return deleted_count

    # ------------------------------------------------------------------
    # Decay Helper
    # ------------------------------------------------------------------

    def _compute_decay(
        self,
        age_days: int,
        accepted: int,
        overall_score: float
    ) -> float:
        """
        Compute decay score using the formula from episodic_memory_design.md:

            decay = exp(-λ × age_in_days)
            if accepted == 0:    decay *= 0.6
            if overall_score < 0.5: decay *= 0.7

        Parameters
        ----------
        age_days : int
            Age of the episode in days.
        accepted : int
            Whether the episode was accepted (1) or rejected (0).
        overall_score : float
            Quality score (0.0–1.0).

        Returns
        -------
        float
            Computed decay score clamped to [0.0, 1.0].
        """
        decay = math.exp(-DECAY_LAMBDA * age_days)
        if accepted == 0:
            decay *= 0.6
        if overall_score < 0.5:
            decay *= 0.7
        return round(min(max(decay, 0.0), 1.0), 6)

    # ------------------------------------------------------------------
    # Diagnostic APIs
    # ------------------------------------------------------------------

    def get_failed_facts(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """
        Return top-N KG fact IDs most frequently associated with
        rejected episodes (accepted = 0).

        Supports: Fact Quality Gate (Galib's component).

        Returns
        -------
        list of dict with keys: fact_id, rejection_count
        """
        cur = self._conn.cursor()
        cur.execute("""
            SELECT ef.fact_id, COUNT(*) AS rejection_count
            FROM episode_facts ef
            JOIN episodes e ON ef.episode_id = e.episode_id
            WHERE e.accepted = 0
            GROUP BY ef.fact_id
            ORDER BY rejection_count DESC
            LIMIT ?
        """, (top_n,))
        rows = [dict(r) for r in cur.fetchall()]
        log.debug("Top %d failure-causing fact IDs returned.", len(rows))
        return rows

    def get_high_performing_topics(self) -> List[Dict[str, Any]]:
        """
        Return topic-level statistics: average score, acceptance rate,
        and question count.

        Supports: Adaptive KG expansion decisions.

        Returns
        -------
        list of dict with keys: topic, avg_score, acceptance_rate, question_count
        """
        cur = self._conn.cursor()
        cur.execute("""
            SELECT
                topic,
                ROUND(AVG(overall_score), 4)          AS avg_score,
                ROUND(AVG(accepted), 4)               AS acceptance_rate,
                COUNT(*)                               AS question_count
            FROM episodes
            GROUP BY topic
            ORDER BY avg_score DESC;
        """)
        rows = [dict(r) for r in cur.fetchall()]
        log.debug("%d topic(s) returned.", len(rows))
        return rows

    def get_recent_topic_trend(
        self,
        topic: str,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Return a time-series of scores for a specific topic over the
        last `days` days to track performance evolution.

        Parameters
        ----------
        topic : str
            Topic name to filter on.
        days : int
            Number of days to look back.

        Returns
        -------
        list of dict with keys: timestamp, overall_score, accepted
        """
        cur = self._conn.cursor()
        since = (
            datetime.datetime.now() - datetime.timedelta(days=days)
        ).isoformat()
        cur.execute("""
            SELECT timestamp, overall_score, accepted
            FROM episodes
            WHERE topic = ? AND timestamp >= ?
            ORDER BY timestamp ASC
        """, (topic, since))
        rows = [dict(r) for r in cur.fetchall()]
        log.debug("%d data point(s) for topic='%s' over last %d day(s).", len(rows), topic, days)
        return rows

    def get_episodes_by_time_range(
        self,
        start_dt: str,
        end_dt: str,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve all episodes within a given ISO 8601 datetime range.

        Parameters
        ----------
        start_dt : str
            Start datetime in ISO format (e.g., '2026-01-01T00:00:00').
        end_dt : str
            End datetime in ISO format.

        Returns
        -------
        list of dict — full episode rows.
        """
        cur = self._conn.cursor()
        cur.execute("""
            SELECT * FROM episodes
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """, (start_dt, end_dt))
        rows = [dict(r) for r in cur.fetchall()]
        log.debug("%d episode(s) in range [%s -> %s].", len(rows), start_dt, end_dt)
        return rows

    def get_episode_detail(self, episode_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a full episode record including linked fact IDs, MCQs,
        and rejection logs.

        Parameters
        ----------
        episode_id : str
            Target episode UUID.

        Returns
        -------
        dict or None
        """
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM episodes WHERE episode_id = ?", (episode_id,))
        row = cur.fetchone()
        if row is None:
            return None
        detail = dict(row)

        cur.execute("SELECT fact_id FROM episode_facts WHERE episode_id = ?", (episode_id,))
        detail["fact_ids"] = [r["fact_id"] for r in cur.fetchall()]

        cur.execute("SELECT * FROM episode_mcqs WHERE episode_id = ?", (episode_id,))
        detail["mcqs"] = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM rejection_logs WHERE episode_id = ?", (episode_id,))
        detail["rejection_logs"] = [dict(r) for r in cur.fetchall()]

        return detail

    def summary(self) -> None:
        """Print a summary of the episodic memory database."""
        cur = self._conn.cursor()
        print("\n-----  Episodic Memory Summary  -----")
        for table in ["episodes", "episode_facts", "episode_mcqs", "rejection_logs"]:
            cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
            count = cur.fetchone()["cnt"]
            print(f"  {table:<20} : {count} row(s)")
        cur.execute("SELECT COUNT(*) AS cnt FROM episodes WHERE accepted = 1")
        accepted = cur.fetchone()["cnt"]
        cur.execute("SELECT ROUND(AVG(overall_score), 4) AS avg FROM episodes")
        avg_score = cur.fetchone()["avg"]
        print(f"  Accepted episodes   : {accepted}")
        print(f"  Avg overall_score   : {avg_score}")
        print("-------------------------------------\n")

    def write_feedback(
        self,
        episode_id: str,
        mcq_id: str,
        fact_ids: Optional[List[str]] = None,
        rating: int = 3,
        category: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> int:
        now_str = datetime.datetime.now().isoformat()
        cur = self._conn.cursor()
        cur.execute("""
            INSERT INTO feedback
                (episode_id, mcq_id, fact_ids, rating, category, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            episode_id, mcq_id,
            json.dumps(fact_ids or [], ensure_ascii=False),
            rating, category, comment, now_str,
        ))
        self._conn.commit()
        log.debug("Feedback written: episode=%s mcq=%s rating=%d", episode_id[:8], mcq_id[:12], rating)
        return cur.lastrowid

    def get_feedback_stats(self) -> Dict[str, Any]:
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM feedback")
        total = cur.fetchone()["cnt"]
        cur.execute("SELECT ROUND(AVG(rating), 2) AS avg FROM feedback")
        avg_row = cur.fetchone()
        avg = avg_row["avg"] if avg_row["avg"] else 0.0
        cur.execute("""
            SELECT rating, COUNT(*) AS cnt FROM feedback
            GROUP BY rating ORDER BY rating
        """)
        dist = {str(r["rating"]): r["cnt"] for r in cur.fetchall()}
        cur.execute("SELECT category, COUNT(*) AS cnt FROM feedback WHERE category IS NOT NULL GROUP BY category ORDER BY cnt DESC")
        cats = [dict(r) for r in cur.fetchall()]
        return {"total": total, "avg_rating": avg, "distribution": dist, "categories": cats}

    def get_feedback_for_fact(self, fact_id: str) -> List[Dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("""
            SELECT f.* FROM feedback f
            WHERE f.fact_ids LIKE ?
            ORDER BY f.created_at DESC
        """, (f"%{fact_id}%",))
        return [dict(r) for r in cur.fetchall()]

    def get_feedback_by_rating(self, min_rating: int = 1, limit: int = 50) -> List[Dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("""
            SELECT f.*, e.topic, e.input_question
            FROM feedback f
            JOIN episodes e ON f.episode_id = e.episode_id
            WHERE f.rating <= ?
            ORDER BY f.created_at DESC
            LIMIT ?
        """, (min_rating, limit))
        return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Dataset export  (for Priority 3: fine-tuning data)
    # ------------------------------------------------------------------

    def export_dataset(
        self,
        min_quality: float = 0.70,
        limit: int = 1000,
        include_feedback: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Export accepted MCQs as a structured dataset suitable for
        fine-tuning a judge model.

        Each record joins:
          - episode_mcqs (question, options, scores, difficulty)
          - episodes    (topic, timestamp)
          - feedback    (avg user rating, count)

        Parameters
        ----------
        min_quality : float
            Minimum quality_score to include (default 0.70).
        limit : int
            Maximum rows to return.
        include_feedback : bool
            If True, join user feedback and include avg_rating/rating_count.

        Returns
        -------
        list of dict
        """
        cur = self._conn.cursor()

        query = """
            SELECT
                m.mcq_id,
                m.episode_id,
                m.question,
                m.options,
                m.correct_answer,
                m.difficulty,
                m.quality_score,
                m.format_score,
                m.grounding_score,
                m.clarity_score,
                m.distractor_score,
                m.regeneration_round,
                e.topic,
                e.timestamp
            FROM episode_mcqs m
            JOIN episodes e ON m.episode_id = e.episode_id
            WHERE m.quality_score >= ?
            ORDER BY e.timestamp DESC
            LIMIT ?
        """
        cur.execute(query, (min_quality, limit))
        rows = [dict(r) for r in cur.fetchall()]

        # Parse options JSON string back to dict
        for row in rows:
            if isinstance(row.get("options"), str):
                try:
                    row["options"] = json.loads(row["options"])
                except (json.JSONDecodeError, TypeError):
                    row["options"] = {}

        # Augment with feedback stats if requested
        if include_feedback and rows:
            mcq_ids = [r["mcq_id"] for r in rows]
            placeholders = ",".join("?" * len(mcq_ids))
            cur.execute(f"""
                SELECT mcq_id,
                       ROUND(AVG(rating), 2) AS avg_rating,
                       COUNT(*)              AS rating_count
                FROM feedback
                WHERE mcq_id IN ({placeholders})
                GROUP BY mcq_id
            """, mcq_ids)
            feedback_map = {r["mcq_id"]: r for r in cur.fetchall()}
            for row in rows:
                fb = feedback_map.get(row["mcq_id"], {})
                row["avg_rating"] = fb.get("avg_rating")
                row["rating_count"] = fb.get("rating_count", 0)

        log.debug("export_dataset: %d rows (min_quality=%.2f)", len(rows), min_quality)
        return rows

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        log.debug("Database connection closed.")
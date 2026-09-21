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


# ---------------------------------------------------------------------------
# Constants & Source Tier Weighting (Task 4.2)
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = "memory.db"
DECAY_LAMBDA = 0.05
PRESERVE_SCORE_THRESHOLD = 0.85
PRESERVE_ACCEPTED = 1

SOURCE_TIER_WEIGHTS: Dict[int, float] = {
    1: 1.00,  # Official Gazette / Government / Constitutional bodies
    2: 0.85,  # Intergovernmental / Official International Orgs
    3: 0.70,  # Primary Institutional / Banglapedia / Wikipedia
    4: 0.55,  # Reputable News / Media
    5: 0.30,  # Secondary / Unverified / Blogs
}


def compute_evidence_weight(
    source_tier: Optional[int],
    retrieved_via: str = "live",
    is_pre_cutoff: bool = True,
    max_allowed_tier: int = 4,
) -> float:
    """
    Compute evidence weight for a retrieved fact based on its source tier and temporal proof.

    Guideline §7.1:
      Tier 1: 1.00 (Official Gazette/BPSC)
      Tier 2: 0.85 (Intergovernmental/UN/WB)
      Tier 3: 0.70 (Banglapedia/Wikipedia)
      Tier 4: 0.55 (Reputable News)
      Tier 5: 0.30 (Secondary/Blogs)

    If source_tier > max_allowed_tier or not is_pre_cutoff, weight is 0.0 (inadmissible).
    """
    if not is_pre_cutoff:
        return 0.0
    tier = source_tier if source_tier is not None else 4
    if tier > max_allowed_tier:
        return 0.0
    base_weight = SOURCE_TIER_WEIGHTS.get(tier, 0.30)
    if retrieved_via == "wayback":
        base_weight = min(1.0, base_weight * 1.0)
    return round(base_weight, 4)


def is_admissible_evidence(
    source_tier: Optional[int],
    is_pre_cutoff: bool = True,
    max_allowed_tier: int = 4,
) -> bool:
    """Check if evidence meets tier floor and temporal cutoff admissibility criteria."""
    if not is_pre_cutoff:
        return False
    tier = source_tier if source_tier is not None else 4
    return tier <= max_allowed_tier


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
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()
        print(f"[EpisodicMemory] Connected to database: {os.path.abspath(db_path)}")

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
                regeneration_round  INTEGER DEFAULT 0,
                FOREIGN KEY (episode_id) REFERENCES episodes(episode_id)
            );
        """)

        # ---- Table 4: rejection_logs -----------------------------------
        # FIX: added `fact_id`. Previously this table only ever got rows
        # via update_episode()'s optional rejection_reason/judge_feedback
        # side-effect, at episode granularity — nothing in the pipeline
        # ever actually called that path with real per-MCQ failure data,
        # so this table stayed empty (0 rows) even in runs with real
        # rejections, and get_failed_facts() (which used to join through
        # episodes.accepted instead) could never see failures that
        # happened inside an otherwise-accepted episode. `fact_id` plus
        # the new log_rejection() method below let a caller log a
        # specific fact's failure directly, and get_failed_facts() now
        # reads this column instead.
        # ---- Table 5: web_retrieval_logs -------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS web_retrieval_logs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id          TEXT,
                url                 TEXT NOT NULL,
                archive_permalink   TEXT,
                html_digest         TEXT,
                snapshot_date       TEXT,
                retrieved_via       TEXT NOT NULL DEFAULT 'live',
                source_tier         INTEGER,
                evidence_weight     REAL DEFAULT 1.0,
                observed_at         TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'accepted'
            );
        """)

        self._conn.commit()
        print("[EpisodicMemory] Schema initialized (5 tables ready).")

    # ------------------------------------------------------------------
    # Web Retrieval Provenance Logging (Task 4.1 & 4.2)
    # ------------------------------------------------------------------

    def log_web_retrieval(
        self,
        url: str,
        retrieved_via: str = "live",
        source_tier: Optional[int] = None,
        observed_at: Optional[str] = None,
        status: str = "accepted",
        episode_id: Optional[str] = None,
        archive_permalink: Optional[str] = None,
        html_digest: Optional[str] = None,
        snapshot_date: Optional[str] = None,
        evidence_weight: Optional[float] = None,
    ) -> int:
        """
        Record a web retrieval provenance event in episodic memory with source tier evidence weighting.
        """
        now_str = observed_at or datetime.datetime.now().isoformat()
        if evidence_weight is None:
            is_pre = (status != "rejected")
            evidence_weight = compute_evidence_weight(source_tier, retrieved_via, is_pre)

        cur = self._conn.cursor()
        cur.execute("""
            INSERT INTO web_retrieval_logs
                (episode_id, url, archive_permalink, html_digest,
                 snapshot_date, retrieved_via, source_tier, evidence_weight, observed_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            episode_id, url, archive_permalink, html_digest,
            snapshot_date, retrieved_via, source_tier, evidence_weight, now_str, status
        ))
        self._conn.commit()
        return cur.lastrowid

    def get_web_retrieval_provenance(self, url_or_digest: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve provenance record for a URL or HTML digest.
        """
        cur = self._conn.cursor()
        cur.execute("""
            SELECT * FROM web_retrieval_logs
            WHERE url = ? OR archive_permalink = ? OR html_digest = ?
            ORDER BY id DESC LIMIT 1
        """, (url_or_digest, url_or_digest, url_or_digest))
        row = cur.fetchone()
        return dict(row) if row else None

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
                     difficulty, quality_score, regeneration_round)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                mcq_id, episode_id,
                mcq.get("question", ""),
                options_json,
                mcq.get("correct_answer", ""),
                mcq.get("difficulty", "medium"),
                mcq.get("quality_score", 0.0),
                mcq.get("regeneration_round", 0),
            ))

        self._conn.commit()
        print(f"[write_episode] [OK] Episode {episode_id[:8]}… written | topic={topic} | score={overall_score} | accepted={accepted}")
        return episode_id

    # ------------------------------------------------------------------
    # 5.1b  log_rejection()  (FIX — real per-fact rejection logging)
    # ------------------------------------------------------------------

    def log_rejection(
        self,
        episode_id: str,
        fact_id: Optional[str],
        reason: str,
        judge_feedback: Optional[str] = None,
    ) -> None:
        """
        Record a single fact/MCQ-level rejection directly, independent of
        update_episode()'s episode-level accept/reject bookkeeping.

        This is the missing link that made Stage 10 diagnostics
        (get_failed_facts(), "MCQ-level Failed Fact IDs") always report
        "None" even in runs with real rejections: nothing in the pipeline
        was calling anything that inserted rows into rejection_logs with
        a fact_id attached. Callers should invoke this once per rejected
        MCQ/fact (e.g. from mcq_quality.py's failed evaluations), not
        just once per episode.

        Parameters
        ----------
        episode_id : str
            The episode this rejection belongs to (for audit trail).
        fact_id : str, optional
            The specific KG fact that failed. May be None for
            episode-wide failures with no single fact to blame.
        reason : str
            Rejection code(s)/reason, e.g. "E-DIST, E-UNSUP".
        judge_feedback : str, optional
            Free-text judge/evaluator feedback, if available.
        """
        now_str = datetime.datetime.now().isoformat()
        cur = self._conn.cursor()
        cur.execute("""
            INSERT INTO rejection_logs
                (episode_id, fact_id, reason, judge_feedback, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (episode_id, fact_id, reason, judge_feedback, now_str))
        self._conn.commit()

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
        print(f"[retrieve_similar_episodes] [SEARCH] Found {len(output)} similar episode(s) | topic={topic} | min_score={min_score}")
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
            print(f"[update_episode] [WARN]  Episode {episode_id} not found.")
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
        print(f"[update_episode] [UPDATE] Episode {episode_id[:8]}… updated | accepted={new_accepted} | score={new_score} | decay={new_decay:.4f}")

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

        Note on decay_score staleness (fixed)
        --------------------------------------
        The `decay_score` column is only ever written at write_episode()
        time (age_days=0) or when update_episode() happens to run — it does
        NOT advance on its own as days pass. Previously this method trusted
        that stored column directly in its WHERE clause, so an
        old-but-never-updated episode (the common case — update_episode()
        is only called on score<0.5 episodes elsewhere in the pipeline)
        kept its write-time decay_score forever and could never be pruned
        via the decay_threshold condition, only via the separate
        age/accepted condition. This method now recomputes decay_score live
        from each episode's actual current age before applying either
        condition, and persists the refreshed value back to the row (except
        during dry_run, which stays side-effect-free) so the column keeps
        meaning what its name says between prune passes too.
        """
        cur = self._conn.cursor()
        now = datetime.datetime.now()
        cutoff_date = (now - datetime.timedelta(days=max_age_days)).isoformat()

        # Pull every episode that isn't unconditionally preserved, then
        # recompute decay live — cannot filter by decay in SQL any more
        # since it now depends on wall-clock time, not a stored column.
        cur.execute("""
            SELECT episode_id, timestamp, overall_score, accepted, decay_score
            FROM episodes
            WHERE NOT (accepted = 1 AND overall_score > ?)
        """, (PRESERVE_SCORE_THRESHOLD,))

        candidates = []
        refreshed  = []
        for row in cur.fetchall():
            created_at = datetime.datetime.fromisoformat(row["timestamp"])
            age_days = (now - created_at).days
            live_decay = self._compute_decay(age_days, row["accepted"], row["overall_score"])

            should_delete = (
                live_decay < decay_threshold
                or (row["timestamp"] < cutoff_date and row["accepted"] == 0)
            )
            if should_delete:
                candidates.append({
                    "episode_id":    row["episode_id"],
                    "overall_score": row["overall_score"],
                    "accepted":      row["accepted"],
                    "decay_score":   live_decay,
                })
            else:
                refreshed.append((live_decay, row["episode_id"]))

        if dry_run:
            print(f"[forget_old_episodes] [DRY RUN] — {len(candidates)} episode(s) would be pruned "
                  f"(decay recomputed live from current age).")
            for c in candidates:
                print(f"   → {c['episode_id'][:8]}… | score={c['overall_score']} | "
                      f"accepted={c['accepted']} | decay={c['decay_score']:.4f}")
            return len(candidates)

        # Persist refreshed decay_score for episodes that survive this pass,
        # so the column stays live even between prune runs.
        if refreshed:
            cur.executemany(
                "UPDATE episodes SET decay_score = ? WHERE episode_id = ?", refreshed
            )

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
        print(f"[forget_old_episodes] [PRUNED] {deleted_count} episode(s) | threshold={decay_threshold} | max_age={max_age_days}d")
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
        Return top-N KG fact IDs most frequently associated with a
        logged rejection (see log_rejection()).

        FIX: previously this joined episode_facts -> episodes WHERE
        accepted = 0, which only ever caught a fact if its ENTIRE
        episode (a whole topic/batch) was rejected. A fact whose MCQ
        failed mcq_quality evaluation inside an otherwise-accepted
        episode (the normal case — one bad MCQ among several good ones
        for the same topic) was invisible to it, which is why this
        always returned "Top 0" even in runs with real per-MCQ
        failures. Now reads directly from rejection_logs.fact_id, which
        log_rejection() populates at the correct (per-fact) granularity.

        Supports: Fact Quality Gate (Galib's component).

        Returns
        -------
        list of dict with keys: fact_id, rejection_count
        """
        cur = self._conn.cursor()
        cur.execute("""
            SELECT fact_id, COUNT(*) AS rejection_count
            FROM rejection_logs
            WHERE fact_id IS NOT NULL
            GROUP BY fact_id
            ORDER BY rejection_count DESC
            LIMIT ?
        """, (top_n,))
        rows = [dict(r) for r in cur.fetchall()]
        print(f"[get_failed_facts] [INFO] Top {len(rows)} failure-causing fact IDs returned.")
        return rows

    def get_high_performing_topics(self, include_placeholders: bool = False) -> List[Dict[str, Any]]:
        """
        Return topic-level statistics: average score, acceptance rate,
        and question count.

        FIX: episodes written for a topic that never actually reached
        MCQ generation this run (skipped because the MCQ_BUDGET cap was
        hit) are written as "placeholder" episodes — see main-pipeline's
        Stage 7, which tags them generation_config.source =
        "pipeline_fallback" and scores them from raw fact mcq_readiness,
        never from a real judge verdict. Previously this method averaged
        those in right alongside real, judge-evaluated episodes with no
        way to tell them apart, so a topic with ZERO MCQs generated this
        run (e.g. "Constitution"/"Government"/"General" when the budget
        ran out) could still show up in Stage 10 diagnostics with a
        real-looking avg_score/acceptance_rate as if generation had
        actually happened for it. Placeholders are now excluded by
        default; pass include_placeholders=True to see everything
        (e.g. for debugging what the fallback scores looked like).

        Supports: Adaptive KG expansion decisions.

        Returns
        -------
        list of dict with keys: topic, avg_score, acceptance_rate, question_count
        """
        cur = self._conn.cursor()
        if include_placeholders:
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
        else:
            cur.execute("SELECT topic, overall_score, accepted, generation_config FROM episodes")
            by_topic: Dict[str, List[sqlite3.Row]] = {}
            for r in cur.fetchall():
                try:
                    cfg = json.loads(r["generation_config"] or "{}")
                except (TypeError, ValueError):
                    cfg = {}
                if cfg.get("source") == "pipeline_fallback":
                    continue
                by_topic.setdefault(r["topic"], []).append(r)
            rows = []
            for topic, recs in by_topic.items():
                n = len(recs)
                rows.append({
                    "topic": topic,
                    "avg_score": round(sum(r["overall_score"] for r in recs) / n, 4),
                    "acceptance_rate": round(sum(r["accepted"] for r in recs) / n, 4),
                    "question_count": n,
                })
            rows.sort(key=lambda r: r["avg_score"], reverse=True)
        print(f"[get_high_performing_topics] [INFO] {len(rows)} topic(s) returned"
              f"{'' if include_placeholders else ' (placeholder episodes excluded)'}.")
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
        print(f"[get_recent_topic_trend] [INFO] {len(rows)} data point(s) for topic='{topic}' over last {days} day(s).")
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
        print(f"[get_episodes_by_time_range] [INFO] {len(rows)} episode(s) in range [{start_dt} → {end_dt}].")
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

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        print("[EpisodicMemory] Database connection closed.")
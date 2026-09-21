import networkx as nx
import uuid
import datetime
import os
import pickle

from bcs.logging_config import get_logger

log = get_logger(__name__)


# Allowed entity subtypes per the KG schema specification
ALLOWED_ENTITY_SUBTYPES = {
    "PERSON", "ORGANIZATION", "COUNTRY", "PLACE",
    # Extended subtypes for richer BCS GK coverage
    "CITY", "RIVER", "MOUNTAIN", "FOREST", "OCEAN",
    "INSTITUTION", "EVENT", "DOCUMENT", "TREATY",
    "AWARD", "LANGUAGE", "CURRENCY", "ANIMAL", "PLANT",
}


class KnowledgeGraphBuilder:
    def __init__(self):
        self.graph = nx.MultiDiGraph()
        self.topic_stats = {}

    # -----------------------------
    # Utility
    # -----------------------------
    def _generate_id(self, prefix):
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def _normalize_name(self, name):
        return name.strip().replace(" ", "_")

    # -----------------------------
    # Node Creation
    # -----------------------------

    def add_entity(self, name, subtype):
        # Validate subtype against allowed set
        subtype_upper = subtype.upper()
        if subtype_upper not in ALLOWED_ENTITY_SUBTYPES:
            log.warning("subtype '%s' not in allowed set. Mapping to closest or using as-is.",
                        subtype)

        normalized = self._normalize_name(name)
        node_id = f"ENTITY_{normalized}"

        if not self.graph.has_node(node_id):
            self.graph.add_node(
                node_id,
                type="ENTITY",
                subtype=subtype_upper,
                name=name
            )
        return node_id

    def add_fact(self, text,
                 language_confidence=1.0,
                 extraction_confidence=1.0,
                 source_reliability=1.0,
                 temporal_freshness=1.0):

        fact_id = self._generate_id("FACT")

        self.graph.add_node(
            fact_id,
            type="FACT",
            text=text,
            language_confidence=language_confidence,
            extraction_confidence=extraction_confidence,
            source_reliability=source_reliability,
            temporal_freshness=temporal_freshness,
            created_at=str(datetime.datetime.now())
        )

        return fact_id

    def add_source(self, url, publisher=None):
        # Use UUID instead of hash to avoid hash instability
        node_id = f"SOURCE_{uuid.uuid5(uuid.NAMESPACE_URL, url)}"

        if not self.graph.has_node(node_id):
            self.graph.add_node(
                node_id,
                type="SOURCE",
                url=url,
                publisher=publisher
            )
        return node_id

    def add_topic(self, topic_name):
        topic_id = f"TOPIC_{topic_name.upper()}"

        if not self.graph.has_node(topic_id):
            self.graph.add_node(
                topic_id,
                type="TOPIC",
                name=topic_name
            )

            self.topic_stats[topic_id] = {
                "fact_count": 0,
                "question_count": 0,
                "unmapped_facts": 0,
                "unmapped_questions": 0
            }

        return topic_id

    def add_question(self, text, question_type="original", topic=None,
                     source_fact_ids=None, parent_question_id=None):
        """
        Add a Question node to the Knowledge Graph.

        Parameters
        ----------
        text : str
            The question text (Bangla or English).
        question_type : str
            One of 'original', 'generated', 'refined'.
        topic : str, optional
            Topic name to link this question to via ABOUT edge.
        source_fact_ids : list of str, optional
            Fact node IDs this question ASKS_FOR.
        parent_question_id : str, optional
            If this is a refined question, the ID of the parent question
            (creates DERIVED_FROM or REFINES edge).

        Returns
        -------
        str
            The generated question node ID.
        """
        question_id = self._generate_id("QUESTION")

        self.graph.add_node(
            question_id,
            type="QUESTION",
            text=text,
            question_type=question_type,
            created_at=str(datetime.datetime.now())
        )

        # Link question → topic
        if topic:
            topic_id = self.add_topic(topic)
            self.link(question_id, topic_id, "ABOUT")
            if topic_id in self.topic_stats:
                self.topic_stats[topic_id]["question_count"] += 1

        # Link question → fact(s) it asks about
        mapped = False
        if source_fact_ids:
            for fact_id in source_fact_ids:
                if self.graph.has_node(fact_id):
                    self.link(question_id, fact_id, "ASKS_FOR")
                    mapped = True

        # Track unmapped questions
        if not mapped and topic:
            topic_id = f"TOPIC_{topic.upper()}"
            if topic_id in self.topic_stats:
                self.topic_stats[topic_id]["unmapped_questions"] += 1

        # Link to parent question if refined
        if parent_question_id and self.graph.has_node(parent_question_id):
            if question_type == "refined":
                self.link(question_id, parent_question_id, "REFINES")
            else:
                self.link(question_id, parent_question_id, "DERIVED_FROM")

        return question_id

    # -----------------------------
    # Edge Creation
    # -----------------------------

    def link(self, source, target, relation):
        self.graph.add_edge(source, target, relation=relation)

    # -----------------------------
    # Fact Insertion Pipeline
    # -----------------------------

    def insert_fact_pipeline(self,
                             fact_text,
                             subject_entities,
                             object_entities,
                             topic,
                             source_url,
                             publisher=None,
                             quality_scores=None):

        if quality_scores is None:
            quality_scores = {
                "language_confidence": 1.0,
                "extraction_confidence": 1.0,
                "source_reliability": 1.0,
                "temporal_freshness": 1.0
            }

        fact_id = self.add_fact(fact_text, **quality_scores)
        topic_id = self.add_topic(topic)
        source_id = self.add_source(source_url, publisher)

        # Link fact → topic
        self.link(fact_id, topic_id, "ABOUT")

        # Link fact → source
        self.link(fact_id, source_id, "SUPPORTED_BY")

        # Link subject entities
        for ent_name, subtype in subject_entities:
            ent_id = self.add_entity(ent_name, subtype)
            self.link(ent_id, fact_id, "SUBJECT_OF")

        # Link object entities
        for ent_name, subtype in object_entities:
            ent_id = self.add_entity(ent_name, subtype)
            self.link(fact_id, ent_id, "OBJECT_IS")

        # Update topic stats safely
        if topic_id in self.topic_stats:
            self.topic_stats[topic_id]["fact_count"] += 1

        return fact_id

    # -----------------------------
    # Unmapped Fact Tracking
    # -----------------------------

    def mark_fact_unmapped(self, fact_id, topic):
        """
        Mark a fact as unmapped (not linked to any question or entity properly).
        Increments the unmapped_facts counter for the given topic.
        """
        topic_id = f"TOPIC_{topic.upper()}"
        if topic_id in self.topic_stats:
            self.topic_stats[topic_id]["unmapped_facts"] += 1
        if self.graph.has_node(fact_id):
            self.graph.nodes[fact_id]["unmapped"] = True

    def get_unmapped_facts(self):
        """Return all fact nodes marked as unmapped."""
        unmapped = []
        for node_id, data in self.graph.nodes(data=True):
            if data.get("type") == "FACT" and data.get("unmapped", False):
                unmapped.append(node_id)
        return unmapped

    # -----------------------------
    # Adaptive Growth Logic
    # -----------------------------

    def analyze_topic_density(self, fact_threshold=50, question_threshold=30,
                               unmapped_threshold=10):
        """
        Analyze fact and question density per topic and recommend
        expansion strategies.

        Returns
        -------
        list of tuples
            (topic_id, action, reason) where action is
            'EXPAND_DEPTH' or 'EXPAND_WIDTH'.
        """
        expansion_recommendations = []

        for topic_id, stats in self.topic_stats.items():
            # High fact density → expand topic depth (subtopics)
            if stats["fact_count"] > fact_threshold:
                expansion_recommendations.append(
                    (topic_id, "EXPAND_DEPTH",
                     f"fact_count={stats['fact_count']} exceeds threshold={fact_threshold}")
                )
            # High question density → expand topic depth
            if stats["question_count"] > question_threshold:
                expansion_recommendations.append(
                    (topic_id, "EXPAND_DEPTH",
                     f"question_count={stats['question_count']} exceeds threshold={question_threshold}")
                )
            # Repeated unmapped items → expand topic width (new related topics)
            total_unmapped = stats["unmapped_facts"] + stats.get("unmapped_questions", 0)
            if total_unmapped > unmapped_threshold:
                expansion_recommendations.append(
                    (topic_id, "EXPAND_WIDTH",
                     f"unmapped_items={total_unmapped} exceeds threshold={unmapped_threshold}")
                )

        return expansion_recommendations

    def get_topic_stats(self):
        """Return a copy of topic statistics dictionary."""
        return dict(self.topic_stats)

    # -----------------------------
    # Query Helpers
    # -----------------------------

    def get_facts_by_topic(self, topic_name):
        """Return all Fact node IDs linked to a given topic."""
        topic_id = f"TOPIC_{topic_name.upper()}"
        fact_ids = []
        for src, tgt, data in self.graph.edges(data=True):
            if tgt == topic_id and data.get("relation") == "ABOUT":
                if self.graph.nodes[src].get("type") == "FACT":
                    fact_ids.append(src)
        return fact_ids

    def get_questions_by_topic(self, topic_name):
        """Return all Question node IDs linked to a given topic."""
        topic_id = f"TOPIC_{topic_name.upper()}"
        question_ids = []
        for src, tgt, data in self.graph.edges(data=True):
            if tgt == topic_id and data.get("relation") == "ABOUT":
                if self.graph.nodes[src].get("type") == "QUESTION":
                    question_ids.append(src)
        return question_ids

    def get_fact_data(self, fact_id):
        """Return the data dict for a given fact node, or None."""
        if self.graph.has_node(fact_id):
            return dict(self.graph.nodes[fact_id])
        return None

    def update_fact_attribute(self, fact_id, key, value):
        """Update a single attribute on a fact node."""
        if self.graph.has_node(fact_id):
            self.graph.nodes[fact_id][key] = value

    def remove_fact(self, fact_id):
        """
        Remove a fact node and all its edges from the graph.
        Updates topic stats accordingly.
        """
        if not self.graph.has_node(fact_id):
            return

        # Find and decrement topic stats
        for _, tgt, data in list(self.graph.edges(fact_id, data=True)):
            if data.get("relation") == "ABOUT" and tgt in self.topic_stats:
                self.topic_stats[tgt]["fact_count"] = max(
                    0, self.topic_stats[tgt]["fact_count"] - 1
                )

        self.graph.remove_node(fact_id)

    # -----------------------------
    # Snapshot Save (Fixed Version)
    # -----------------------------

    def save_snapshot(self, folder="snapshots"):
        os.makedirs(folder, exist_ok=True)

        filename = f"kg_{datetime.datetime.now().strftime('%Y_%m')}.gpickle"
        path = os.path.join(folder, filename)

        # Save both graph AND topic_stats so adaptive growth survives reload
        snapshot = {
            "graph": self.graph,
            "topic_stats": dict(self.topic_stats),
        }
        with open(path, "wb") as f:
            pickle.dump(snapshot, f)

        log.info("Knowledge Graph saved to %s (%d nodes, %d edges, %d topic stats)",
                 path, self.graph.number_of_nodes(), self.graph.number_of_edges(),
                 len(self.topic_stats))
        return path

    # -----------------------------
    # Snapshot Load
    # -----------------------------

    def load_snapshot(self, filepath):
        try:
            with open(filepath, "rb") as f:
                data = pickle.load(f)

            # Handle both old format (plain graph) and new format (dict with topic_stats)
            if isinstance(data, dict) and "graph" in data:
                self.graph = data["graph"]
                self.topic_stats = data.get("topic_stats", {})
            else:
                # Legacy format: data is the graph itself
                self.graph = data
                self.topic_stats = {}
                # Rebuild topic_stats from loaded graph
                self._rebuild_topic_stats()

            log.info("Knowledge Graph loaded from %s (%d nodes, %d topic stats)",
                     filepath, self.graph.number_of_nodes(), len(self.topic_stats))
        except (FileNotFoundError, pickle.UnpicklingError, EOFError) as e:
            log.error("Could not load '%s': %s", filepath, e)

    def _rebuild_topic_stats(self):
        """Rebuild topic_stats from graph data (used after loading legacy snapshots)."""
        self.topic_stats = {}
        for node_id, data in self.graph.nodes(data=True):
            if data.get("type") == "TOPIC":
                self.topic_stats[node_id] = {
                    "fact_count": 0,
                    "question_count": 0,
                    "unmapped_facts": 0,
                    "unmapped_questions": 0,
                }
        # Count facts and questions per topic
        for src, tgt, edge_data in self.graph.edges(data=True):
            if edge_data.get("relation") == "ABOUT" and tgt in self.topic_stats:
                src_type = self.graph.nodes[src].get("type")
                if src_type == "FACT":
                    self.topic_stats[tgt]["fact_count"] += 1
                elif src_type == "QUESTION":
                    self.topic_stats[tgt]["question_count"] += 1

    # -----------------------------
    # Debug Summary (Very Useful)
    # -----------------------------

    def summary(self):
        log.debug("----- Knowledge Graph Summary -----")
        log.debug("Total Nodes: %d", self.graph.number_of_nodes())
        log.debug("Total Edges: %d", self.graph.number_of_edges())

        type_count = {}
        for _, data in self.graph.nodes(data=True):
            t = data.get("type", "UNKNOWN")
            type_count[t] = type_count.get(t, 0) + 1

        for k, v in type_count.items():
            log.debug("  %s: %d", k, v)
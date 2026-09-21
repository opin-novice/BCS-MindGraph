"""
test_kg_bitemporal.py
=====================
Unit and integration test suite for Bitemporal Knowledge Graph Engine (Task 3.1 & 3.3).
"""

import unittest
import datetime
from kg_builder import KnowledgeGraphBuilder


class TestKGBitemporal(unittest.TestCase):

    def setUp(self):
        self.kg = KnowledgeGraphBuilder()

    def test_bitemporal_schema_attributes(self):
        """Verify that bitemporal attributes are correctly recorded on nodes."""
        fact_id = self.kg.insert_fact_pipeline(
            fact_text="Person A holds Office X",
            subject_entities=[("Person A", "PERSON")],
            object_entities=[("Office X", "ORGANIZATION")],
            topic="Bangladesh Affairs",
            source_url="https://gov.bd/office_x",
            publisher="BPSC",
            relation="holds_position",
            valid_from="2020-01-01",
            valid_to="2022-12-31",
            source_published_at="2020-01-05",
            source_tier=1,
            status="accepted"
        )

        data = self.kg.get_fact_data(fact_id)
        self.assertIsNotNone(data)
        self.assertEqual(data["relation"], "holds_position")
        self.assertEqual(data["valid_from"], "2020-01-01")
        self.assertEqual(data["valid_to"], "2022-12-31")
        self.assertEqual(data["source_tier"], 1)
        self.assertIn("observed_at", data)

        source_data = self.kg.get_fact_source_data(fact_id)
        self.assertIsNotNone(source_data)
        self.assertEqual(source_data["source_published_at"], "2020-01-05")
        self.assertEqual(source_data["source_tier"], 1)

    def test_bitemporal_tuple_extraction(self):
        """Verify that get_bitemporal_tuple extracts all 9 elements cleanly."""
        fact_id = self.kg.insert_fact_pipeline(
            fact_text="Dr. Kalam became Chairman of Commission",
            subject_entities=[("Dr. Kalam", "PERSON")],
            object_entities=[("Commission", "ORGANIZATION")],
            topic="International Affairs",
            source_url="https://official.org/announcement",
            publisher="Govt Gazette",
            relation="holds_position",
            valid_from="2018-05-10",
            valid_to=None,
            source_published_at="2018-05-12",
            source_tier=1,
        )

        bt_tuple = self.kg.get_bitemporal_tuple(fact_id)
        self.assertIsNotNone(bt_tuple)
        self.assertEqual(bt_tuple["fact_id"], fact_id)
        self.assertEqual(bt_tuple["subject"], "Dr. Kalam")
        self.assertEqual(bt_tuple["relation"], "holds_position")
        self.assertEqual(bt_tuple["object"], "Commission")
        self.assertEqual(bt_tuple["valid_from"], "2018-05-10")
        self.assertIsNone(bt_tuple["valid_to"])
        self.assertIsNotNone(bt_tuple["observed_at"])
        self.assertEqual(bt_tuple["source_published_at"], "2018-05-12")
        self.assertEqual(bt_tuple["source_tier"], 1)

    def test_version_chain_supersession(self):
        """Verify versioning auto-closes prior open facts when a newer fact is inserted."""
        fact1_id = self.kg.insert_fact_pipeline(
            fact_text="Minister X heads Ministry Y",
            subject_entities=[("Minister X", "PERSON")],
            object_entities=[("Ministry Y", "ORGANIZATION")],
            topic="Bangladesh Affairs",
            source_url="https://gov.bd/gazette1",
            relation="holds_position",
            valid_from="2015-01-01",
            valid_to=None,
            source_tier=1,
        )

        fact2_id = self.kg.insert_fact_pipeline(
            fact_text="Minister Z succeeded Minister X at Ministry Y",
            subject_entities=[("Minister X", "PERSON")],
            object_entities=[("Ministry Y", "ORGANIZATION")],
            topic="Bangladesh Affairs",
            source_url="https://gov.bd/gazette2",
            relation="holds_position",
            valid_from="2020-06-01",
            valid_to=None,
            source_tier=1,
        )

        f1_data = self.kg.get_fact_data(fact1_id)
        f2_data = self.kg.get_fact_data(fact2_id)

        self.assertEqual(f1_data["status"], "superseded")
        self.assertEqual(f1_data["valid_to"], "2020-06-01")
        self.assertEqual(f2_data["status"], "accepted")
        self.assertIsNone(f2_data["valid_to"])

    def test_as_of_respects_valid_to_even_with_unknown_valid_from(self):
        """
        as_of() must honour the upper bound (valid_to) of a fact even
        when its valid_from is unknown -- an unversioned start does not
        mean "valid forever," only "valid since before recorded history."
        A stale operator-precedence bug here used to let `or vf is None`
        short-circuit the whole containment check, so any fact with an
        unknown start was returned as valid for ANY query date, even
        one long after its valid_to had already passed.
        """
        fact_id = self.kg.insert_fact_pipeline(
            fact_text="Minister Q headed Ministry R",
            subject_entities=[("Minister Q", "PERSON")],
            object_entities=[("Ministry R", "ORGANIZATION")],
            topic="Bangladesh Affairs",
            source_url="https://gov.bd/gazette9",
            relation="holds_position",
            valid_from="2010-01-01",
            valid_to="2012-01-01",
            source_tier=1,
        )
        self.kg._register_version(
            list(self.kg.graph.in_edges(fact_id))[0][0],
            "holds_position",
            fact_id,
        )
        # Simulate an unknown/unrecorded start date on an otherwise
        # normal, already-closed fact.
        self.kg.graph.nodes[fact_id]["valid_from"] = None

        # A query well after valid_to must NOT return this expired fact.
        result = self.kg.as_of("Minister Q", "holds_position", "2020-01-01")
        self.assertIsNone(
            result,
            "as_of() returned a fact past its valid_to just because "
            "valid_from was unknown"
        )

        # A query still inside [unknown-start, valid_to) must return it.
        result = self.kg.as_of("Minister Q", "holds_position", "2011-06-01")
        self.assertIsNotNone(result)
        self.assertEqual(result["fact_id"], fact_id)

    def test_remove_fact_purges_version_index(self):
        """
        remove_fact() must not leave a dangling entry in the version
        chain -- as_of() and get_fact_history() index straight into
        self.graph.nodes[fid] and would raise KeyError on a fact_id
        that was removed from the graph but not from the index.
        """
        fact1_id = self.kg.insert_fact_pipeline(
            fact_text="Minister S headed Ministry T",
            subject_entities=[("Minister S", "PERSON")],
            object_entities=[("Ministry T", "ORGANIZATION")],
            topic="Bangladesh Affairs",
            source_url="https://gov.bd/gazette5",
            relation="holds_position",
            valid_from="2015-01-01",
            valid_to=None,
            source_tier=1,
        )
        fact2_id = self.kg.insert_fact_pipeline(
            fact_text="Minister U succeeded Minister S at Ministry T",
            subject_entities=[("Minister S", "PERSON")],
            object_entities=[("Ministry T", "ORGANIZATION")],
            topic="Bangladesh Affairs",
            source_url="https://gov.bd/gazette6",
            relation="holds_position",
            valid_from="2020-06-01",
            valid_to=None,
            source_tier=1,
        )

        self.kg.remove_fact(fact2_id)

        # Must not raise KeyError, and must not resurrect the removed fact.
        history = self.kg.get_fact_history("Minister S", "holds_position")
        history_ids = [h["fact_id"] for h in history]
        self.assertNotIn(fact2_id, history_ids)
        self.assertIn(fact1_id, history_ids)

        # fact2's insertion already closed fact1's valid_to at
        # 2020-06-01 (normal supersession); removing fact2 does not
        # reopen fact1. Query a date inside fact1's still-valid window
        # to confirm the index is queryable without KeyError.
        result = self.kg.as_of("Minister S", "holds_position", "2018-01-01")
        self.assertIsNotNone(result)
        self.assertEqual(result["fact_id"], fact1_id)

    def test_graph_snapshot(self):
        """Verify get_graph_snapshot isolates graph state at t_cutoff without leakage."""
        # Valid fact as of 2023-04-19
        fid_valid = self.kg.insert_fact_pipeline(
            fact_text="Person A holds Office X",
            subject_entities=[("Person A", "PERSON")],
            object_entities=[("Office X", "ORGANIZATION")],
            topic="Government",
            source_url="https://gov.bd/official",
            relation="holds_position",
            valid_from="2021-01-01",
            valid_to=None,
            source_published_at="2021-01-05",
            source_tier=1,
        )

        # Future fact (valid after t_cutoff) -> MUST BE EXCLUDED
        fid_future = self.kg.insert_fact_pipeline(
            fact_text="Person B holds Office X",
            subject_entities=[("Person B", "PERSON")],
            object_entities=[("Office X", "ORGANIZATION")],
            topic="Government",
            source_url="https://gov.bd/future",
            relation="holds_position",
            valid_from="2024-01-01",
            valid_to=None,
            source_published_at="2024-01-05",
            source_tier=1,
        )

        # Closed fact (expired before t_cutoff) -> MUST BE EXCLUDED
        fid_past = self.kg.insert_fact_pipeline(
            fact_text="Person C held Office X",
            subject_entities=[("Person C", "PERSON")],
            object_entities=[("Office X", "ORGANIZATION")],
            topic="Government",
            source_url="https://gov.bd/past",
            relation="holds_position",
            valid_from="2015-01-01",
            valid_to="2020-01-01",
            source_published_at="2015-01-05",
            source_tier=1,
        )

        snapshot_kg = self.kg.get_graph_snapshot(t_cutoff="2023-04-19")

        snapshot_facts = {
            n for n, d in snapshot_kg.graph.nodes(data=True)
            if d.get("type") == "FACT"
        }

        self.assertIn(fid_valid, snapshot_facts)
        self.assertNotIn(fid_future, snapshot_facts)
        self.assertNotIn(fid_past, snapshot_facts)
        self.assertEqual(len(snapshot_facts), 1)

    def test_multihop_path_query(self):
        """Verify multi-hop path query traversal respects temporal cutoff guards."""
        # Fact 1: Entity A -> Entity B (valid 2020)
        self.kg.insert_fact_pipeline(
            fact_text="Entity A connected to Entity B",
            subject_entities=[("Entity A", "ORGANIZATION")],
            object_entities=[("Entity B", "PERSON")],
            topic="Government",
            source_url="https://gov.bd/p1",
            relation="member_of",
            valid_from="2020-01-01",
            source_published_at="2020-01-05",
            source_tier=1,
        )

        # Fact 2: Entity B -> Entity C (valid 2021)
        self.kg.insert_fact_pipeline(
            fact_text="Entity B connected to Entity C",
            subject_entities=[("Entity B", "PERSON")],
            object_entities=[("Entity C", "INSTITUTION")],
            topic="Government",
            source_url="https://gov.bd/p2",
            relation="holds_position",
            valid_from="2021-01-01",
            source_published_at="2021-01-05",
            source_tier=1,
        )

        # Fact 3: Entity C -> Entity D (valid 2024 - post cutoff)
        self.kg.insert_fact_pipeline(
            fact_text="Entity C connected to Entity D",
            subject_entities=[("Entity C", "INSTITUTION")],
            object_entities=[("Entity D", "CITY")],
            topic="Government",
            source_url="https://gov.bd/p3",
            relation="located_in",
            valid_from="2024-01-01",
            source_published_at="2024-01-05",
            source_tier=1,
        )

        # 1. Path check under 2023-04-19 cutoff: A -> B -> C should exist
        paths_to_c = self.kg.find_paths_between_entities("Entity A", "Entity C", as_of_date="2023-04-19")
        self.assertGreaterEqual(len(paths_to_c), 1)

        # 2. Path check under 2023-04-19 cutoff: A -> D must NOT exist due to post-cutoff Fact 3
        paths_to_d = self.kg.find_paths_between_entities("Entity A", "Entity D", as_of_date="2023-04-19")
        self.assertEqual(len(paths_to_d), 0)

    def test_entity_neighborhood(self):
        """Verify multi-hop neighborhood extraction around an entity under temporal snapshot."""
        self.kg.insert_fact_pipeline(
            fact_text="Alpha leads Beta",
            subject_entities=[("Alpha", "PERSON")],
            object_entities=[("Beta", "ORGANIZATION")],
            topic="Government",
            source_url="https://gov.bd/n1",
            relation="led_by",
            valid_from="2019-01-01",
            source_published_at="2019-01-05",
            source_tier=1,
        )

        nb = self.kg.get_entity_neighborhood("Alpha", radius=2, as_of_date="2023-04-19")
        self.assertIsNotNone(nb)
        self.assertEqual(nb["entity_name"], "Alpha")
        self.assertGreaterEqual(len(nb["connected_entities"]), 1)
        self.assertEqual(nb["connected_entities"][0]["name"], "Beta")
        self.assertGreaterEqual(len(nb["connected_facts"]), 1)

    def test_source_tier_validation(self):
        """Verify valid tiers (1-5) are accepted and invalid tiers raise ValueError."""
        with self.assertRaises(ValueError):
            self.kg.add_fact(text="Invalid tier fact", source_tier=99)

        with self.assertRaises(ValueError):
            self.kg.add_source(url="https://invalid.com", source_tier=10)


if __name__ == "__main__":
    unittest.main()

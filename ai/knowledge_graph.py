"""
Survey Knowledge Graph
========================
Adapted from AI Surveyor ml/knowledge_graph.py.
Builds a persistent networkx graph from survey research data:
  - Nodes: clients (persons), parcels, documents (deeds/plats), jobs
  - Edges: owns, adjoins, recorded_in, surveyed_by

Powers:
  1. "Who are the adjoiners for this property?" — graph traversal
  2. "What jobs involved this person?" — entity lookup
  3. "Which parcels share a boundary?" — adjacency chain
"""

import json
import re
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict

import networkx as nx
from loguru import logger

from ai import AI_DATA_DIR


# ── Paths ───────────────────────────────────────────────────────────────────

_GRAPH_FILE = AI_DATA_DIR / "survey_knowledge_graph.json"


# ══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE GRAPH
# ══════════════════════════════════════════════════════════════════════════════

class SurveyKnowledgeGraph:
    """Property/owner/adjacency knowledge graph for surveying."""

    def __init__(self):
        self.G = nx.Graph()
        self._load()

    # ── Persistence ─────────────────────────────────────────────────────

    def _load(self):
        """Load graph from JSON if it exists."""
        if _GRAPH_FILE.exists():
            try:
                data = json.loads(_GRAPH_FILE.read_text(encoding="utf-8"))
                self.G = nx.node_link_graph(data)
                logger.debug(f"Loaded graph: {self.G.number_of_nodes()} nodes, "
                             f"{self.G.number_of_edges()} edges")
            except Exception as e:
                logger.warning(f"Graph load failed, starting fresh: {e}")
                self.G = nx.Graph()

    def save(self):
        """Persist graph to JSON."""
        data = nx.node_link_data(self.G)
        _GRAPH_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info(f"💾 Saved graph: {self.G.number_of_nodes()} nodes, "
                     f"{self.G.number_of_edges()} edges")

    # ── Node Management ─────────────────────────────────────────────────

    def add_person(self, name: str, **props) -> str:
        """Add a person/entity node. Returns the node ID."""
        node_id = self._normalize_name(name)
        if not node_id:
            return ""
        if self.G.has_node(node_id):
            self.G.nodes[node_id].update(props)
        else:
            self.G.add_node(node_id, type="person", name=name,
                           added=datetime.now().isoformat(), **props)
        return node_id

    def add_job(self, job_number: int, client_name: str,
                job_type: str = "", **props) -> str:
        """Add a survey job node."""
        node_id = f"job_{job_number}"
        if self.G.has_node(node_id):
            self.G.nodes[node_id].update(props)
        else:
            self.G.add_node(node_id, type="job", job_number=job_number,
                           client_name=client_name, job_type=job_type,
                           added=datetime.now().isoformat(), **props)
        return node_id

    def add_document(self, doc_ref: str, doc_type: str = "deed",
                     **props) -> str:
        """Add a document (deed or plat) node."""
        node_id = f"doc_{doc_ref}"
        if not self.G.has_node(node_id):
            self.G.add_node(node_id, type=doc_type, ref=doc_ref,
                           added=datetime.now().isoformat(), **props)
        return node_id

    # ── Edge Management ─────────────────────────────────────────────────

    def add_relationship(self, source: str, target: str,
                         rel_type: str, **props):
        """Add a relationship edge between two nodes."""
        if (source and target and
                self.G.has_node(source) and self.G.has_node(target)):
            self.G.add_edge(source, target, relationship=rel_type, **props)

    def add_adjacency(self, person_a: str, person_b: str,
                      job_number: int = 0):
        """Record that two people/properties are adjacent."""
        id_a = self._normalize_name(person_a)
        id_b = self._normalize_name(person_b)
        if id_a and id_b and id_a != id_b:
            self.add_person(person_a)
            self.add_person(person_b)
            self.G.add_edge(id_a, id_b, relationship="adjoins",
                           discovered_in=job_number)

    # ── Population from Deed Helper research.json files ─────────────────

    def populate_from_research_sessions(self, survey_data_path: str) -> dict:
        """Build the knowledge graph from the Deed Helper's own research.json files.

        Scans the Survey Data folder structure for completed research sessions
        and extracts client-adjoiner relationships.
        """
        if not survey_data_path or not Path(survey_data_path).exists():
            return {"success": False, "error": "Survey data path not found"}

        survey = Path(survey_data_path)
        persons_added = 0
        jobs_added = 0
        adjacencies_added = 0

        for range_dir in survey.iterdir():
            if not range_dir.is_dir() or range_dir.name.startswith("00"):
                continue
            for job_dir in range_dir.iterdir():
                if not job_dir.is_dir():
                    continue
                m = re.match(r'^(\d{4})\s+(.*)', job_dir.name)
                if not m:
                    continue
                job_number = int(m.group(1))
                client_name = m.group(2).strip()

                for sub_dir in job_dir.iterdir():
                    if not sub_dir.is_dir():
                        continue
                    mt = re.match(r'^\d+-01-([A-Z]+)\s', sub_dir.name)
                    if not mt:
                        continue
                    job_type = mt.group(1)
                    research_file = sub_dir / "E Research" / "research.json"
                    if not research_file.exists():
                        continue

                    try:
                        data = json.loads(
                            research_file.read_text(encoding="utf-8")
                        )
                        subjects = data.get("subjects", [])
                        client_id = self.add_person(client_name)
                        if not client_id:
                            continue
                        persons_added += 1

                        adj_count = sum(
                            1 for s in subjects if s.get("type") == "adjoiner"
                        )
                        job_id = self.add_job(
                            job_number, client_name, job_type,
                            deed_count=sum(
                                1 for s in subjects if s.get("deed_saved")
                            ),
                            plat_count=sum(
                                1 for s in subjects if s.get("plat_saved")
                            ),
                            estimated_adjoiners=adj_count,
                        )
                        jobs_added += 1
                        self.add_relationship(client_id, job_id, "client_of")

                        for s in subjects:
                            if s.get("type") == "adjoiner":
                                adj_name = s.get("name", "")
                                adj_id = self.add_person(adj_name)
                                if adj_id:
                                    persons_added += 1
                                    self.add_adjacency(
                                        client_name, adj_name, job_number
                                    )
                                    adjacencies_added += 1
                                    self.add_relationship(
                                        adj_id, job_id, "adjoiner_in"
                                    )
                    except Exception:
                        continue

        self.save()
        stats = {
            "success": True,
            "total_nodes": self.G.number_of_nodes(),
            "total_edges": self.G.number_of_edges(),
            "persons_added": persons_added,
            "jobs_added": jobs_added,
            "adjacencies_added": adjacencies_added,
        }
        logger.success(
            f"✅ Graph populated: {stats['total_nodes']} nodes, "
            f"{stats['total_edges']} edges"
        )
        return stats

    def populate_from_archive(self, archive_data_path: str = "") -> dict:
        """Build the knowledge graph from archived training data JSON."""
        for name in [
            "full_archive_training_data.json",
            "archive_training_data.json",
        ]:
            path = AI_DATA_DIR / name
            if path.exists():
                archive_data_path = str(path)
                break

        if not archive_data_path or not Path(archive_data_path).exists():
            return {"success": False, "error": "No archive data found"}

        records = json.loads(
            Path(archive_data_path).read_text(encoding="utf-8")
        )
        logger.info(f"📊 Populating graph from {len(records)} archive records")

        persons_added = 0
        jobs_added = 0
        adjacencies_added = 0
        docs_added = 0

        for r in records:
            job_num = r["job_number"]
            client = r["client_name"]
            job_type = r.get("job_type", "")

            client_id = self.add_person(client)
            if not client_id:
                continue
            persons_added += 1

            job_id = self.add_job(
                job_num, client, job_type,
                deed_count=r.get("deed_count", 0),
                plat_count=r.get("plat_count", 0),
                estimated_adjoiners=r.get("estimated_adjoiners", 0),
            )
            jobs_added += 1
            self.add_relationship(client_id, job_id, "client_of")

            for adj_name in r.get("adjoiner_names", []):
                adj_id = self.add_person(adj_name)
                if adj_id:
                    persons_added += 1
                    self.add_adjacency(client, adj_name, job_num)
                    adjacencies_added += 1
                    self.add_relationship(adj_id, job_id, "adjoiner_in")

            for bp in r.get("book_page_refs", []):
                book = bp.get("book", "")
                page = bp.get("page", "")
                if book:
                    doc_ref = f"{book}-{page}" if page else book
                    doc_id = self.add_document(
                        doc_ref, "deed", book=book, page=page,
                        filename=bp.get("file", ""),
                    )
                    docs_added += 1
                    self.add_relationship(client_id, doc_id, "recorded_in")

        self.save()
        stats = {
            "success": True,
            "total_nodes": self.G.number_of_nodes(),
            "total_edges": self.G.number_of_edges(),
            "persons_added": persons_added,
            "jobs_added": jobs_added,
            "adjacencies_added": adjacencies_added,
            "documents_added": docs_added,
        }
        logger.success(
            f"✅ Graph populated: {stats['total_nodes']} nodes, "
            f"{stats['total_edges']} edges"
        )
        return stats

    # ── Queries ─────────────────────────────────────────────────────────

    def get_adjoiners(self, person_name: str) -> list[dict]:
        """Find all known adjoiners for a person/property."""
        node_id = self._normalize_name(person_name)
        if not node_id or not self.G.has_node(node_id):
            node_id = self._fuzzy_find(person_name)
            if not node_id:
                return []

        adjoiners = []
        for neighbor in self.G.neighbors(node_id):
            edge = self.G.edges[node_id, neighbor]
            if edge.get("relationship") == "adjoins":
                node_data = self.G.nodes[neighbor]
                adjoiners.append({
                    "name": node_data.get("name", neighbor),
                    "type": node_data.get("type", ""),
                    "job_discovered": edge.get("discovered_in", ""),
                })
        return adjoiners

    def get_person_jobs(self, person_name: str) -> list[dict]:
        """Find all jobs involving this person (as client or adjoiner)."""
        node_id = self._normalize_name(person_name)
        if not node_id or not self.G.has_node(node_id):
            node_id = self._fuzzy_find(person_name)
            if not node_id:
                return []

        jobs = []
        for neighbor in self.G.neighbors(node_id):
            node_data = self.G.nodes[neighbor]
            if node_data.get("type") == "job":
                edge = self.G.edges[node_id, neighbor]
                jobs.append({
                    "job_number": node_data.get("job_number"),
                    "client_name": node_data.get("client_name"),
                    "job_type": node_data.get("job_type"),
                    "role": edge.get("relationship", ""),
                })
        return jobs

    def get_adjacency_chain(self, person_name: str,
                            depth: int = 2) -> dict:
        """Get the adjacency chain — who adjoins whom, up to N hops."""
        node_id = self._normalize_name(person_name)
        if not node_id or not self.G.has_node(node_id):
            node_id = self._fuzzy_find(person_name)
            if not node_id:
                return {"center": person_name, "chain": []}

        chain = []
        visited = {node_id}
        queue = [(node_id, 0)]

        while queue:
            current, d = queue.pop(0)
            if d >= depth:
                continue
            for neighbor in self.G.neighbors(current):
                edge = self.G.edges[current, neighbor]
                if edge.get("relationship") != "adjoins":
                    continue
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                node_data = self.G.nodes[neighbor]
                chain.append({
                    "name": node_data.get("name", neighbor),
                    "depth": d + 1,
                    "connected_to": self.G.nodes[current].get("name", current),
                })
                queue.append((neighbor, d + 1))

        return {
            "center": person_name,
            "chain": chain,
            "total_in_chain": len(chain),
        }

    def search_persons(self, query: str, limit: int = 20) -> list[dict]:
        """Search for persons by partial name match."""
        query_lower = query.lower()
        results = []
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") != "person":
                continue
            name = data.get("name", "")
            if query_lower in name.lower():
                adj_count = sum(
                    1 for n in self.G.neighbors(node_id)
                    if self.G.edges[node_id, n].get("relationship") == "adjoins"
                )
                job_count = sum(
                    1 for n in self.G.neighbors(node_id)
                    if self.G.nodes[n].get("type") == "job"
                )
                results.append({
                    "name": name,
                    "adjoiners": adj_count,
                    "jobs": job_count,
                })
                if len(results) >= limit:
                    break
        results.sort(key=lambda r: r["adjoiners"], reverse=True)
        return results

    def graph_stats(self) -> dict:
        """Get summary statistics about the knowledge graph."""
        if self.G.number_of_nodes() == 0:
            return {"empty": True, "total_nodes": 0, "total_edges": 0}

        type_counts = Counter(
            data.get("type", "unknown")
            for _, data in self.G.nodes(data=True)
        )
        rel_counts = Counter(
            data.get("relationship", "unknown")
            for _, _, data in self.G.edges(data=True)
        )

        person_degrees = []
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") == "person":
                adj_count = sum(
                    1 for n in self.G.neighbors(node_id)
                    if self.G.edges[node_id, n].get("relationship") == "adjoins"
                )
                if adj_count > 0:
                    person_degrees.append(
                        (data.get("name", node_id), adj_count)
                    )
        person_degrees.sort(key=lambda x: x[1], reverse=True)

        return {
            "total_nodes": self.G.number_of_nodes(),
            "total_edges": self.G.number_of_edges(),
            "node_types": dict(type_counts),
            "relationship_types": dict(rel_counts),
            "most_connected": [
                {"name": n, "adjoiners": c}
                for n, c in person_degrees[:15]
            ],
            "graph_file": str(_GRAPH_FILE),
            "file_size_mb": round(
                _GRAPH_FILE.stat().st_size / 1024 / 1024, 2
            ) if _GRAPH_FILE.exists() else 0,
        }

    # ── Helpers ──────────────────────────────────────────────────────────

    def _normalize_name(self, name: str) -> str:
        """Normalize a person name to a consistent node ID."""
        if not name:
            return ""
        clean = re.sub(r'\s+', ' ', name.strip())
        clean = re.sub(r'\s+\d+[-\s]?\d*$', '', clean)
        if ' to ' in clean:
            clean = clean.split(' to ')[0].strip()
        return clean.lower().replace(' ', '_').replace(',', '')

    def _fuzzy_find(self, query: str) -> str | None:
        """Fuzzy-find a node by partial name match."""
        query_lower = query.lower().replace(',', '').replace(' ', '_')
        if self.G.has_node(query_lower):
            return query_lower
        last_name = query.split(",")[0].strip().lower().replace(' ', '_')
        for node_id in self.G.nodes():
            if last_name in node_id:
                return node_id
        return None

    # ── Entity Resolution ───────────────────────────────────────────────

    @staticmethod
    def _jaro_winkler(s1: str, s2: str, p: float = 0.1) -> float:
        """Compute Jaro-Winkler similarity (0.0–1.0). Pure Python."""
        if s1 == s2:
            return 1.0
        if not s1 or not s2:
            return 0.0

        len1, len2 = len(s1), len(s2)
        match_dist = max(len1, len2) // 2 - 1
        if match_dist < 0:
            match_dist = 0

        s1_matches = [False] * len1
        s2_matches = [False] * len2

        matches = 0
        transpositions = 0

        for i in range(len1):
            start = max(0, i - match_dist)
            end = min(i + match_dist + 1, len2)
            for j in range(start, end):
                if s2_matches[j] or s1[i] != s2[j]:
                    continue
                s1_matches[i] = True
                s2_matches[j] = True
                matches += 1
                break

        if matches == 0:
            return 0.0

        k = 0
        for i in range(len1):
            if not s1_matches[i]:
                continue
            while not s2_matches[k]:
                k += 1
            if s1[i] != s2[k]:
                transpositions += 1
            k += 1

        jaro = (matches / len1 + matches / len2 +
                (matches - transpositions / 2) / matches) / 3

        prefix = 0
        for i in range(min(4, len1, len2)):
            if s1[i] == s2[i]:
                prefix += 1
            else:
                break

        return jaro + prefix * p * (1 - jaro)

    def find_duplicates(self, threshold: float = 0.88,
                        limit: int = 200) -> list[dict]:
        """Find likely duplicate person nodes using Jaro-Winkler similarity."""
        person_nodes = [
            (nid, data.get("name", nid))
            for nid, data in self.G.nodes(data=True)
            if data.get("type") == "person"
        ]

        comp_entries = []
        for nid, name in person_nodes:
            comp = self._name_for_comparison(name)
            if comp and len(comp) >= 3:
                comp_entries.append((nid, name, comp))

        token_blocks = defaultdict(list)
        for idx, (nid, name, comp) in enumerate(comp_entries):
            for token in comp.split():
                if len(token) >= 3:
                    token_blocks[token].append(idx)

        candidates = []
        seen_pairs = set()

        for token, indices in token_blocks.items():
            if len(indices) < 2 or len(indices) > 500:
                continue
            for i in range(len(indices)):
                if len(candidates) >= limit:
                    break
                idx_a = indices[i]
                nid_a, name_a, comp_a = comp_entries[idx_a]
                for j in range(i + 1, len(indices)):
                    idx_b = indices[j]
                    pair_key = (min(idx_a, idx_b), max(idx_a, idx_b))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    nid_b, name_b, comp_b = comp_entries[idx_b]
                    sim = self._jaro_winkler(comp_a, comp_b)
                    if sim >= threshold:
                        edges_a = self.G.degree(nid_a)
                        edges_b = self.G.degree(nid_b)
                        candidates.append({
                            "node_a": nid_a, "name_a": name_a,
                            "node_b": nid_b, "name_b": name_b,
                            "similarity": round(sim, 3),
                            "keep": nid_a if edges_a >= edges_b else nid_b,
                            "merge": nid_b if edges_a >= edges_b else nid_a,
                        })
                    if len(candidates) >= limit:
                        break
            if len(candidates) >= limit:
                break

        candidates.sort(key=lambda c: c["similarity"], reverse=True)
        return candidates

    @staticmethod
    def _name_for_comparison(name: str) -> str:
        """Normalize a name for entity resolution comparison."""
        if not name:
            return ""
        clean = re.sub(r'[^\w\s]', ' ', name.lower())
        clean = re.sub(r'\s+', ' ', clean).strip()

        if ',' in name:
            comma_parts = name.split(',', 1)
            last = re.sub(r'[^\w\s]', '', comma_parts[0]).strip().lower()
            first = (re.sub(r'[^\w\s]', '', comma_parts[1]).strip().lower()
                     if len(comma_parts) > 1 else "")
            clean = f"{first} {last}".strip()
        else:
            parts = clean.split()
            clean = ' '.join(sorted(parts))

        return clean

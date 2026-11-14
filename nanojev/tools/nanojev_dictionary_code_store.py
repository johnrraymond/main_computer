#!/usr/bin/env python3
"""Persistent SQLite store for NanoJev dictionary/code objective experiments.

The store separates lexical-node exposure from word<->definition relations.
Definitions selected for general coverage may be trained by unrelated objectives
without revealing which headword owns the definition.  Relation edges remain
independently reservable for later tests.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
from pathlib import Path
import random
import re
import sqlite3
from typing import Sequence


SCHEMA_VERSION = "nanojev-dictionary-code-store-v1"
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache"}


def normalize_word(text: str) -> str | None:
    value = text.strip().lower()
    if not value or " " in value or "_" in value:
        return None
    if not re.fullmatch(r"[a-z][a-z'-]*", value):
        return None
    return value


def definition_tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in WORD_RE.finditer(text)}


def stable_id(*parts: str) -> str:
    raw = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class DictionaryCodeStore:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @contextmanager
    def transaction(self):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS words(
                word_id INTEGER PRIMARY KEY,
                word TEXT NOT NULL UNIQUE,
                coverable INTEGER NOT NULL DEFAULT 0,
                covered INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS definitions(
                definition_id TEXT PRIMARY KEY,
                pos TEXT NOT NULL,
                definition_text TEXT NOT NULL,
                selected_for_coverage INTEGER NOT NULL DEFAULT 0,
                train_count INTEGER NOT NULL DEFAULT 0,
                eval_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS definition_headwords(
                definition_id TEXT NOT NULL REFERENCES definitions(definition_id) ON DELETE CASCADE,
                word_id INTEGER NOT NULL REFERENCES words(word_id) ON DELETE CASCADE,
                PRIMARY KEY(definition_id, word_id)
            );
            CREATE TABLE IF NOT EXISTS definition_terms(
                definition_id TEXT NOT NULL REFERENCES definitions(definition_id) ON DELETE CASCADE,
                word_id INTEGER NOT NULL REFERENCES words(word_id) ON DELETE CASCADE,
                PRIMARY KEY(definition_id, word_id)
            );
            CREATE INDEX IF NOT EXISTS idx_definition_terms_word ON definition_terms(word_id, definition_id);
            CREATE INDEX IF NOT EXISTS idx_definition_headwords_word ON definition_headwords(word_id, definition_id);
            CREATE TABLE IF NOT EXISTS relation_reserve(
                relation_id TEXT PRIMARY KEY,
                word_id INTEGER NOT NULL REFERENCES words(word_id) ON DELETE CASCADE,
                definition_id TEXT NOT NULL REFERENCES definitions(definition_id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'unused'
            );
            CREATE INDEX IF NOT EXISTS idx_relation_reserve_status ON relation_reserve(status);
            CREATE TABLE IF NOT EXISTS code_snippets(
                snippet_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                snippet_text TEXT NOT NULL,
                train_count INTEGER NOT NULL DEFAULT 0,
                eval_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_code_train_count ON code_snippets(train_count, eval_count);
            """
        )
        row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (SCHEMA_VERSION,))
            self.conn.commit()
        elif row[0] != SCHEMA_VERSION:
            raise RuntimeError(f"unsupported lexical DB schema {row[0]!r}; expected {SCHEMA_VERSION!r}")

    def is_empty(self) -> bool:
        return int(self.conn.execute("SELECT COUNT(*) FROM definitions").fetchone()[0]) == 0

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def ingest_synsets(self, synsets: Sequence[dict]) -> dict[str, int]:
        """Ingest WordNet-style synsets and build definition->headword-token edges.

        Coverage is intentionally defined over single-token dictionary headwords that
        occur somewhere in definition text.  Multiword lemmas and headwords that never
        occur in any definition are not admitted to the relation reserve, so a future
        holdout can never silently contain an unexposable lexical node.
        """
        if not synsets:
            raise RuntimeError("cannot ingest an empty dictionary")
        if not self.is_empty():
            return self.stats()

        normalized_by_synset: list[tuple[dict, list[str]]] = []
        all_words: set[str] = set()
        for synset in synsets:
            lemmas = []
            for lemma in synset.get("lemmas") or ():
                normalized = normalize_word(str(lemma))
                if normalized is not None:
                    lemmas.append(normalized)
                    all_words.add(normalized)
            if lemmas:
                normalized_by_synset.append((synset, sorted(set(lemmas))))
        if not all_words:
            raise RuntimeError("dictionary contains no eligible single-token headwords")

        with self.transaction():
            self.conn.executemany("INSERT INTO words(word) VALUES(?)", ((word,) for word in sorted(all_words)))
            word_ids = {
                str(row["word"]): int(row["word_id"])
                for row in self.conn.execute("SELECT word_id, word FROM words")
            }
            for synset, lemmas in normalized_by_synset:
                definition_id = str(synset["id"])
                definition_text = str(synset["definition"]).strip()
                pos = str(synset.get("pos") or "?")
                if not definition_text:
                    continue
                self.conn.execute(
                    "INSERT INTO definitions(definition_id, pos, definition_text) VALUES(?, ?, ?)",
                    (definition_id, pos, definition_text),
                )
                for lemma in lemmas:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO definition_headwords(definition_id, word_id) VALUES(?, ?)",
                        (definition_id, word_ids[lemma]),
                    )
                for token in sorted(definition_tokens(definition_text) & all_words):
                    self.conn.execute(
                        "INSERT OR IGNORE INTO definition_terms(definition_id, word_id) VALUES(?, ?)",
                        (definition_id, word_ids[token]),
                    )

            self.conn.execute(
                "UPDATE words SET coverable=1 WHERE word_id IN (SELECT DISTINCT word_id FROM definition_terms)"
            )
            # Only coverable headwords enter the reserve.  This makes the future
            # no-unseen-word invariant enforceable rather than aspirational.
            rows = self.conn.execute(
                """
                SELECT h.definition_id, h.word_id
                FROM definition_headwords h
                JOIN words w ON w.word_id=h.word_id
                WHERE w.coverable=1
                """
            ).fetchall()
            self.conn.executemany(
                "INSERT INTO relation_reserve(relation_id, word_id, definition_id) VALUES(?, ?, ?)",
                (
                    (stable_id(str(row["definition_id"]), str(row["word_id"])),
                     int(row["word_id"]), str(row["definition_id"]))
                    for row in rows
                ),
            )
        return self.stats()

    def stats(self) -> dict[str, int]:
        q = self.conn.execute
        return {
            "words": int(q("SELECT COUNT(*) FROM words").fetchone()[0]),
            "coverable_words": int(q("SELECT COUNT(*) FROM words WHERE coverable=1").fetchone()[0]),
            "covered_words": int(q("SELECT COUNT(*) FROM words WHERE coverable=1 AND covered=1").fetchone()[0]),
            "uncovered_words": int(q("SELECT COUNT(*) FROM words WHERE coverable=1 AND covered=0").fetchone()[0]),
            "uncoverable_words": int(q("SELECT COUNT(*) FROM words WHERE coverable=0").fetchone()[0]),
            "definitions": int(q("SELECT COUNT(*) FROM definitions").fetchone()[0]),
            "coverage_definitions": int(q("SELECT COUNT(*) FROM definitions WHERE selected_for_coverage=1").fetchone()[0]),
            "relation_reserve_unused": int(q("SELECT COUNT(*) FROM relation_reserve WHERE status='unused'").fetchone()[0]),
            "code_snippets": int(q("SELECT COUNT(*) FROM code_snippets").fetchone()[0]),
        }

    def coverage_complete(self) -> bool:
        """Return True when no uncovered word still has a trainable definition.

        Definitions permanently reserved for evaluation (eval_count>0) must never
        leak back into training.  Such a reservation can leave a lexically
        coverable word with no selectable training definition; that word remains
        uncovered, but it must not block training coverage from advancing.
        """
        row = self.conn.execute(
            """
            SELECT 1
            FROM words w
            WHERE w.coverable=1 AND w.covered=0
              AND EXISTS (
                  SELECT 1
                  FROM definition_terms anchor
                  JOIN definitions d ON d.definition_id=anchor.definition_id
                  WHERE anchor.word_id=w.word_id
                    AND d.selected_for_coverage=0
                    AND d.eval_count=0
              )
            LIMIT 1
            """
        ).fetchone()
        return row is None

    def select_next_coverage_definition(self) -> dict | None:
        """Grow coverage from one trainable uncovered word, maximizing local new-word gain."""
        with self.transaction():
            word = self.conn.execute(
                """
                SELECT w.word_id, w.word
                FROM words w
                WHERE w.coverable=1 AND w.covered=0
                  AND EXISTS (
                      SELECT 1
                      FROM definition_terms anchor
                      JOIN definitions d ON d.definition_id=anchor.definition_id
                      WHERE anchor.word_id=w.word_id
                        AND d.selected_for_coverage=0
                        AND d.eval_count=0
                  )
                ORDER BY w.word
                LIMIT 1
                """
            ).fetchone()
            if word is None:
                return None
            candidates = self.conn.execute(
                """
                SELECT d.definition_id, d.definition_text, d.pos,
                       SUM(CASE WHEN w2.coverable=1 AND w2.covered=0 THEN 1 ELSE 0 END) AS new_gain
                FROM definition_terms anchor
                JOIN definitions d ON d.definition_id=anchor.definition_id
                JOIN definition_terms dt ON dt.definition_id=d.definition_id
                JOIN words w2 ON w2.word_id=dt.word_id
                WHERE anchor.word_id=? AND d.selected_for_coverage=0 AND d.eval_count=0
                GROUP BY d.definition_id
                ORDER BY new_gain DESC, LENGTH(d.definition_text) ASC, d.definition_id ASC
                LIMIT 1
                """,
                (int(word["word_id"]),),
            ).fetchone()
            if candidates is None:
                raise RuntimeError(f"coverable word has no selectable definition: {word['word']}")
            definition_id = str(candidates["definition_id"])
            before = {
                str(row["word"])
                for row in self.conn.execute(
                    """
                    SELECT w.word FROM definition_terms dt
                    JOIN words w ON w.word_id=dt.word_id
                    WHERE dt.definition_id=? AND w.coverable=1 AND w.covered=0
                    """,
                    (definition_id,),
                )
            }
            if str(word["word"]) not in before:
                raise RuntimeError("coverage selection lost its anchor word")
            self.conn.execute(
                "UPDATE definitions SET selected_for_coverage=1 WHERE definition_id=?",
                (definition_id,),
            )
            self.conn.execute(
                """
                UPDATE words SET covered=1
                WHERE word_id IN (SELECT word_id FROM definition_terms WHERE definition_id=?)
                """,
                (definition_id,),
            )
            return {
                "definition_id": definition_id,
                "definition_text": str(candidates["definition_text"]),
                "pos": str(candidates["pos"]),
                "anchor_word": str(word["word"]),
                "new_words": sorted(before),
                "new_gain": len(before),
            }

    def ensure_fresh_coverage_definitions(self, count: int) -> list[dict]:
        if count <= 0:
            return []
        rows = self.conn.execute(
            """
            SELECT definition_id, definition_text, pos
            FROM definitions
            WHERE selected_for_coverage=1 AND train_count=0 AND eval_count=0
            ORDER BY definition_id
            LIMIT ?
            """,
            (count,),
        ).fetchall()
        out = [dict(row) for row in rows]
        while len(out) < count and not self.coverage_complete():
            added = self.select_next_coverage_definition()
            if added is None:
                break
            out.append({
                "definition_id": added["definition_id"],
                "definition_text": added["definition_text"],
                "pos": added["pos"],
            })
        if len(out) < count:
            # Coverage can finish before the smoke is done learning.  Reuse the
            # least-used selected definitions rather than inventing another data source.
            need = count - len(out)
            used_ids = {str(row["definition_id"]) for row in out}
            query = (
                "SELECT definition_id, definition_text, pos FROM definitions "
                "WHERE selected_for_coverage=1 AND eval_count=0 "
                + ("AND definition_id NOT IN (%s) " % ",".join("?" for _ in used_ids) if used_ids else "")
                + "ORDER BY train_count ASC, definition_id ASC LIMIT ?"
            )
            params = [*sorted(used_ids), need]
            out.extend(dict(row) for row in self.conn.execute(query, params).fetchall())
        if len(out) < count:
            raise RuntimeError(f"not enough coverage definitions for training: requested={count} got={len(out)}")
        with self.transaction():
            self.conn.executemany(
                "UPDATE definitions SET train_count=train_count+1 WHERE definition_id=?",
                ((str(row["definition_id"]),) for row in out),
            )
        return out

    def fresh_eval_definitions(self, count: int, rng: random.Random) -> list[dict]:
        if count <= 0:
            return []
        rows = self.conn.execute(
            """
            SELECT definition_id, definition_text, pos
            FROM definitions
            WHERE train_count=0
            ORDER BY definition_id
            """
        ).fetchall()
        if len(rows) < count:
            raise RuntimeError(f"not enough fresh untrained definitions for eval: requested={count} available={len(rows)}")
        chosen = rng.sample(list(rows), count)
        with self.transaction():
            self.conn.executemany(
                "UPDATE definitions SET eval_count=eval_count+1 WHERE definition_id=?",
                ((str(row["definition_id"]),) for row in chosen),
            )
        return [dict(row) for row in chosen]

    def ensure_code_snippets(self, repo_root: Path, minimum: int, *, max_files: int = 2000) -> int:
        current = int(self.conn.execute("SELECT COUNT(*) FROM code_snippets").fetchone()[0])
        if current >= minimum:
            return current
        repo_root = Path(repo_root).resolve(strict=True)
        inserted = 0
        files_seen = 0
        for path in sorted(repo_root.rglob("*.py")):
            if files_seen >= max_files:
                break
            try:
                rel = path.relative_to(repo_root)
            except ValueError:
                continue
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            try:
                if path.stat().st_size > 768 * 1024:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text)
            except (OSError, SyntaxError, UnicodeError):
                continue
            files_seen += 1
            lines = text.splitlines()
            candidates: list[tuple[int, str]] = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.stmt):
                    continue
                if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), (ast.Str, ast.Constant)):
                    value = getattr(node, "value", None)
                    if isinstance(value, ast.Str) or (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                        continue
                start = int(getattr(node, "lineno", 0) or 0)
                end = int(getattr(node, "end_lineno", 0) or 0)
                if start <= 0 or end < start or end - start > 12:
                    continue
                snippet = "\n".join(lines[start - 1:end]).strip()
                if not 16 <= len(snippet) <= 320:
                    continue
                if not any(ch in snippet for ch in "()[]{}=:.+"):
                    continue
                candidates.append((start, snippet))
            for start, snippet in sorted(set(candidates)):
                snippet_id = stable_id(str(rel).replace("\\", "/"), str(start), snippet)
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO code_snippets(snippet_id, source_path, start_line, snippet_text) VALUES(?, ?, ?, ?)",
                    (snippet_id, str(rel).replace("\\", "/"), start, snippet),
                )
                inserted += int(cur.rowcount > 0)
                if current + inserted >= minimum:
                    self.conn.commit()
                    return current + inserted
        self.conn.commit()
        total = int(self.conn.execute("SELECT COUNT(*) FROM code_snippets").fetchone()[0])
        if total < minimum:
            raise RuntimeError(f"repo supplied only {total} usable Python snippets; need {minimum}")
        return total

    def _choose_length_matched_code(self, rows: Sequence[sqlite3.Row], target_lengths: Sequence[int],
                                    rng: random.Random) -> list[sqlite3.Row]:
        if len(rows) < len(target_lengths):
            raise RuntimeError(
                f"not enough code snippets for length matching: requested={len(target_lengths)} available={len(rows)}"
            )
        remaining = list(rows)
        chosen: list[sqlite3.Row] = []
        # Randomize ties, then greedily take the closest character-length match.
        rng.shuffle(remaining)
        for target in target_lengths:
            best_index = min(
                range(len(remaining)),
                key=lambda i: abs(len(str(remaining[i]["snippet_text"])) - int(target)),
            )
            chosen.append(remaining.pop(best_index))
        return chosen

    def training_code_snippets(self, count: int, rng: random.Random,
                               target_lengths: Sequence[int] | None = None) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM code_snippets WHERE eval_count=0 ORDER BY train_count ASC, snippet_id ASC"
        ).fetchall()
        if len(rows) < count:
            raise RuntimeError(f"not enough code snippets for training: requested={count} available={len(rows)}")
        min_count = min(int(row["train_count"]) for row in rows)
        pool = [row for row in rows if int(row["train_count"]) == min_count]
        if len(pool) < count:
            pool = list(rows)
        lengths = list(target_lengths) if target_lengths is not None else [
            len(str(row["snippet_text"])) for row in rng.sample(pool, count)
        ]
        if len(lengths) != count:
            raise ValueError("target_lengths must match requested code snippet count")
        chosen = self._choose_length_matched_code(pool, lengths, rng)
        with self.transaction():
            self.conn.executemany(
                "UPDATE code_snippets SET train_count=train_count+1 WHERE snippet_id=?",
                ((str(row["snippet_id"]),) for row in chosen),
            )
        return [dict(row) for row in chosen]

    def fresh_untrained_code_snippet_count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM code_snippets WHERE train_count=0"
        ).fetchone()[0])

    def fresh_eval_code_snippets(self, count: int, rng: random.Random,
                                 target_lengths: Sequence[int] | None = None) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM code_snippets WHERE train_count=0 ORDER BY snippet_id"
        ).fetchall()
        if len(rows) < count:
            raise RuntimeError(f"not enough fresh untrained code snippets for eval: requested={count} available={len(rows)}")
        lengths = list(target_lengths) if target_lengths is not None else [
            len(str(row["snippet_text"])) for row in rng.sample(list(rows), count)
        ]
        if len(lengths) != count:
            raise ValueError("target_lengths must match requested code snippet count")
        chosen = self._choose_length_matched_code(rows, lengths, rng)
        with self.transaction():
            self.conn.executemany(
                "UPDATE code_snippets SET eval_count=eval_count+1 WHERE snippet_id=?",
                ((str(row["snippet_id"]),) for row in chosen),
            )
        return [dict(row) for row in chosen]

    def reserve_counts(self) -> dict[str, int]:
        return {
            str(row["status"]): int(row["n"])
            for row in self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM relation_reserve GROUP BY status ORDER BY status"
            )
        }

    def fresh_relation_eval_reserve(self, count: int, rng: random.Random) -> list[dict]:
        """Reserve fresh word->definition relations as a permanent evaluation holdout.

        Relation holdouts are disjoint from both prior smoke-eval edges and curriculum
        training edges.  The caller may persist the generated ObjectQuestions and reuse
        the same holdout indefinitely without consuming more reserve rows.
        """
        if count <= 0:
            return []
        rows = self.conn.execute(
            """
            SELECT r.relation_id, w.word, d.definition_id, d.definition_text, d.pos
            FROM relation_reserve r
            JOIN words w ON w.word_id=r.word_id
            JOIN definitions d ON d.definition_id=r.definition_id
            WHERE r.status='unused'
            ORDER BY r.relation_id
            """
        ).fetchall()
        if len(rows) < count:
            raise RuntimeError(
                f"not enough fresh dictionary relations for eval: requested={count} available={len(rows)}"
            )
        chosen = rng.sample(list(rows), count)
        with self.transaction():
            self.conn.executemany(
                "UPDATE relation_reserve SET status='definition_eval' WHERE relation_id=?",
                ((str(row["relation_id"]),) for row in chosen),
            )
        return [dict(row) for row in chosen]

    def training_relation_reserve(self, count: int, rng: random.Random) -> list[dict]:
        """Return relation rows for curriculum training without touching eval holdouts.

        Fresh unused relations are preferred and permanently tagged as training rows.
        Only after the fresh reserve is exhausted are already-trained relation rows
        reused.  Evaluation statuses are never eligible for training.
        """
        if count <= 0:
            return []
        fresh = self.conn.execute(
            """
            SELECT r.relation_id, w.word, d.definition_id, d.definition_text, d.pos
            FROM relation_reserve r
            JOIN words w ON w.word_id=r.word_id
            JOIN definitions d ON d.definition_id=r.definition_id
            WHERE r.status='unused'
            ORDER BY r.relation_id
            LIMIT ?
            """,
            (count,),
        ).fetchall()
        chosen = list(fresh)
        if fresh:
            with self.transaction():
                self.conn.executemany(
                    "UPDATE relation_reserve SET status='definition_train' WHERE relation_id=?",
                    ((str(row["relation_id"]),) for row in fresh),
                )
        if len(chosen) < count:
            need = count - len(chosen)
            trained = self.conn.execute(
                """
                SELECT r.relation_id, w.word, d.definition_id, d.definition_text, d.pos
                FROM relation_reserve r
                JOIN words w ON w.word_id=r.word_id
                JOIN definitions d ON d.definition_id=r.definition_id
                WHERE r.status='definition_train'
                ORDER BY r.relation_id
                """
            ).fetchall()
            fresh_ids = {str(row["relation_id"]) for row in chosen}
            reusable = [row for row in trained if str(row["relation_id"]) not in fresh_ids]
            if len(reusable) < need:
                raise RuntimeError(
                    f"not enough dictionary relations for training: requested={count} available={len(chosen) + len(reusable)}"
                )
            chosen.extend(rng.sample(reusable, need))
        rng.shuffle(chosen)
        return [dict(row) for row in chosen]

    def fresh_covered_relation_reserve(self, count: int, rng: random.Random, *, consume: bool) -> list[dict]:
        """Return holdout edges whose headword and definition tokenyms are covered.

        These edges are guaranteed not to have been exposed *by this store's relation
        objective*.  A mature source head may have historical dictionary exposure from
        experiments predating this DB; callers must not label them historically pristine.
        """
        rows = self.conn.execute(
            """
            SELECT r.relation_id, w.word, d.definition_id, d.definition_text, d.pos
            FROM relation_reserve r
            JOIN words w ON w.word_id=r.word_id
            JOIN definitions d ON d.definition_id=r.definition_id
            WHERE r.status='unused' AND w.covered=1
              AND NOT EXISTS (
                  SELECT 1 FROM definition_terms dt
                  JOIN words tw ON tw.word_id=dt.word_id
                  WHERE dt.definition_id=d.definition_id AND tw.coverable=1 AND tw.covered=0
              )
            ORDER BY r.relation_id
            """
        ).fetchall()
        if len(rows) < count:
            return []
        chosen = rng.sample(list(rows), count)
        if consume:
            with self.transaction():
                self.conn.executemany(
                    "UPDATE relation_reserve SET status='smoke_eval' WHERE relation_id=?",
                    ((str(row["relation_id"]),) for row in chosen),
                )
        return [dict(row) for row in chosen]

    def wrong_definition(self, *, correct_definition_id: str, pos: str, headword: str,
                         rng: random.Random) -> dict:
        rows = self.conn.execute(
            """
            SELECT d.definition_id, d.definition_text, d.pos
            FROM definitions d
            WHERE d.definition_id<>? AND d.pos=?
              AND NOT EXISTS (
                  SELECT 1 FROM definition_headwords h
                  JOIN words w ON w.word_id=h.word_id
                  WHERE h.definition_id=d.definition_id AND w.word=?
              )
            ORDER BY d.definition_id
            """,
            (correct_definition_id, pos, headword.lower()),
        ).fetchall()
        if not rows:
            rows = self.conn.execute(
                """
                SELECT d.definition_id, d.definition_text, d.pos
                FROM definitions d
                WHERE d.definition_id<>?
                  AND NOT EXISTS (
                      SELECT 1 FROM definition_headwords h
                      JOIN words w ON w.word_id=h.word_id
                      WHERE h.definition_id=d.definition_id AND w.word=?
                  )
                ORDER BY d.definition_id
                """,
                (correct_definition_id, headword.lower()),
            ).fetchall()
        if not rows:
            raise RuntimeError("dictionary has no wrong-definition candidate")
        return dict(rng.choice(list(rows)))

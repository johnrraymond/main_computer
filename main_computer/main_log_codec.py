from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Iterator, TextIO

from main_computer.main_log_lex_seed import LEX_STRINGS, PROTECTED_TERMS, SEED_LEX_SHA256, SEED_NAME, SEED_SOURCE_SHA256


FORMAT_VERSION = "mclog-lex-v1"
TOKEN_DELIM = "~"
DEFAULT_SCHEMA: tuple[str, ...] = (
    "at",
    "child",
    "command",
    "cwd",
    "exit_code",
    "ingest_seq",
    "kind",
    "message",
    "path",
    "pid",
    "pid_file",
    "process_name",
    "received_at",
    "restart_count",
    "returncode",
    "root",
    "schema_version",
    "service",
    "source_service",
    "stderr",
    "stdout",
    "stream",
    "__extra__",
)
_RECORD_PREFIX = "!r "


def _base36_encode(value: int) -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    sign = ""
    if value < 0:
        sign = "-"
        value = -value
    if value == 0:
        return "0"
    out = ""
    while value:
        value, remainder = divmod(value, 36)
        out = chars[remainder] + out
    return sign + out


def _base36_decode(text: str) -> int:
    text = str(text).strip().upper()
    if not text:
        raise ValueError("empty base36 value")
    sign = -1 if text.startswith("-") else 1
    if text[0] in "+-":
        text = text[1:]
    value = 0
    for char in text:
        if "0" <= char <= "9":
            digit = ord(char) - ord("0")
        elif "A" <= char <= "Z":
            digit = ord(char) - ord("A") + 10
        else:
            raise ValueError(f"invalid base36 character: {char!r}")
        value = value * 36 + digit
    return sign * value


def parse_log_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_log_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def canonical_json_line(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, default=str)


def _record_time(record: dict[str, Any]) -> datetime | None:
    return parse_log_time(record.get("at")) or parse_log_time(record.get("received_at"))


def filter_records_since_minutes(records: Iterable[dict[str, Any]], minutes: float | None) -> list[dict[str, Any]]:
    items = list(records)
    if minutes is None:
        return items
    stamped: list[tuple[dict[str, Any], datetime | None]] = [(record, _record_time(record)) for record in items]
    times = [stamp for _, stamp in stamped if stamp is not None]
    if not times:
        return []
    cutoff = max(times) - timedelta(minutes=float(minutes))
    return [record for record, stamp in stamped if stamp is not None and stamp >= cutoff]


def _line_records_since_minutes(lines: Iterable[str], minutes: float | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if isinstance(payload, dict):
            records.append(payload)
    return filter_records_since_minutes(records, minutes)


class LexCodec:
    def __init__(
        self,
        *,
        schema: Iterable[str] = DEFAULT_SCHEMA,
        lex_strings: Iterable[str] = LEX_STRINGS,
        protected_terms: Iterable[str] = PROTECTED_TERMS,
        base_time: datetime | None = None,
    ) -> None:
        self.schema = tuple(schema)
        self.lex_strings = tuple(str(item) for item in lex_strings)
        self.protected_terms = tuple(str(item) for item in protected_terms if str(item))
        self.base_time = (base_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self._lex_by_id = {index + 1: value for index, value in enumerate(self.lex_strings)}
        self._lex_code_by_id = {_base36_encode(index + 1): value for index, value in enumerate(self.lex_strings)}
        self._id_by_lex = {value: index + 1 for index, value in enumerate(self.lex_strings)}
        sorted_pairs = sorted(
            ((value, index + 1) for index, value in enumerate(self.lex_strings) if value and "~" not in value),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        # The seed is intentionally filtered so protected terms are not hidden in
        # tokens.  Keep the runtime guard too, because logs can be encoded with a
        # custom lexicon.
        safe_pairs = [
            (value, index)
            for value, index in sorted_pairs
            if not any(term and term in value for term in self.protected_terms)
        ]
        self._pattern = re.compile("|".join(re.escape(value) for value, _ in safe_pairs)) if safe_pairs else None
        self._pattern_ids = {value: index for value, index in safe_pairs}

    def header_lines(self) -> list[str]:
        lines = [
            "!mclog-lex v1",
            f"!seed {SEED_NAME} sha256={SEED_LEX_SHA256} source_sha256={SEED_SOURCE_SHA256}",
            f"!base-time {format_log_time(self.base_time)}",
            "!protect " + " ".join(json.dumps(term) for term in self.protected_terms),
            "!schema " + json.dumps(list(self.schema), separators=(",", ":")),
        ]
        for index, value in enumerate(self.lex_strings, start=1):
            lines.append(f"!lex {_base36_encode(index)} {json.dumps(value, ensure_ascii=False)}")
        lines.append("!records")
        return lines

    def encode_text(self, text: str) -> str:
        if text in self._id_by_lex:
            return f"~{_base36_encode(self._id_by_lex[text])}~"
        if "~" in text:
            text = text.replace("~", "~~")
        if self._pattern is None:
            return text

        def _replace(match: re.Match[str]) -> str:
            value = match.group(0)
            return f"~{_base36_encode(self._pattern_ids[value])}~"

        return self._pattern.sub(_replace, text)

    def decode_text(self, text: str) -> str:
        out: list[str] = []
        index = 0
        length = len(text)
        while index < length:
            char = text[index]
            if char != "~":
                out.append(char)
                index += 1
                continue
            if index + 1 < length and text[index + 1] == "~":
                out.append("~")
                index += 2
                continue
            end = text.find("~", index + 1)
            if end == -1:
                out.append("~")
                index += 1
                continue
            code = text[index + 1 : end].upper()
            replacement = self._lex_code_by_id.get(code)
            if replacement is None:
                out.append(text[index : end + 1])
            else:
                out.append(replacement)
            index = end + 1
        return "".join(out)

    def _encode_timestamp(
        self,
        key: str,
        value: str,
        *,
        previous_timestamps: dict[str, datetime],
    ) -> str | None:
        parsed = parse_log_time(value)
        if parsed is None:
            return None
        if key in previous_timestamps:
            delta_us = int(round((parsed - previous_timestamps[key]).total_seconds() * 1_000_000))
            encoded = "^+" + _base36_encode(delta_us)
        else:
            delta_us = int(round((parsed - self.base_time).total_seconds() * 1_000_000))
            encoded = "^@" + _base36_encode(delta_us)
        previous_timestamps[key] = parsed
        return encoded

    def _decode_timestamp(
        self,
        key: str,
        value: str,
        *,
        previous_timestamps: dict[str, datetime],
    ) -> str:
        if value.startswith("^+"):
            if key not in previous_timestamps:
                raise ValueError(f"timestamp delta for {key!r} has no previous value")
            parsed = previous_timestamps[key] + timedelta(microseconds=_base36_decode(value[2:]))
        elif value.startswith("^@"):
            parsed = self.base_time + timedelta(microseconds=_base36_decode(value[2:]))
        else:
            raise ValueError(f"bad timestamp token: {value!r}")
        previous_timestamps[key] = parsed
        return format_log_time(parsed)

    def encode_record(
        self,
        record: dict[str, Any],
        *,
        previous_values: dict[str, Any],
        previous_timestamps: dict[str, datetime],
        previous_mask: list[int | None],
    ) -> str:
        schema = self.schema
        regular_keys = {key for key in schema if key != "__extra__"}
        extra = {key: value for key, value in record.items() if key not in regular_keys}
        has_extra = bool(extra) and "__extra__" in schema
        mask = 0
        for index, key in enumerate(schema):
            if key == "__extra__":
                if has_extra:
                    mask |= 1 << index
            elif key in record:
                mask |= 1 << index
        values = ["." if previous_mask[0] == mask else _base36_encode(mask)]
        previous_mask[0] = mask

        for key in schema:
            if key == "__extra__":
                if not has_extra:
                    continue
                value = extra
            elif key not in record:
                continue
            else:
                value = record[key]
            if previous_values.get(key) == value and key not in {"at", "received_at", "ingest_seq"}:
                encoded = "="
            elif key in {"at", "received_at"} and isinstance(value, str):
                encoded = self._encode_timestamp(key, value, previous_timestamps=previous_timestamps)
                if encoded is None:
                    encoded = self.encode_text(json.dumps(value, sort_keys=True, default=str))
            elif isinstance(value, bool):
                encoded = "t" if value else "f"
            elif value is None:
                encoded = "n"
            elif isinstance(value, int) and not isinstance(value, bool):
                if key == "ingest_seq" and isinstance(previous_values.get(key), int):
                    encoded = "+" + _base36_encode(value - int(previous_values[key]))
                else:
                    encoded = "#" + _base36_encode(value)
            elif isinstance(value, float):
                encoded = "&" + repr(value)
            else:
                encoded = self.encode_text(json.dumps(value, sort_keys=True, default=str))
            values.append(encoded)

        for key in schema:
            if key == "__extra__":
                if has_extra:
                    previous_values[key] = extra
            elif key in record:
                previous_values[key] = record[key]
        return _RECORD_PREFIX + "\t".join(values)

    def decode_record_line(
        self,
        line: str,
        *,
        previous_values: dict[str, Any],
        previous_timestamps: dict[str, datetime],
        previous_mask: list[int | None],
    ) -> dict[str, Any]:
        if not line.startswith(_RECORD_PREFIX):
            raise ValueError(f"not a record line: {line[:32]!r}")
        payload = line[len(_RECORD_PREFIX) :]
        columns = payload.split("\t")
        if not columns:
            raise ValueError("empty record line")
        if columns[0] == ".":
            if previous_mask[0] is None:
                raise ValueError("mask reuse before mask was established")
            mask = previous_mask[0]
        else:
            mask = _base36_decode(columns[0])
            previous_mask[0] = mask

        present_keys = [key for index, key in enumerate(self.schema) if mask & (1 << index)]
        encoded_values = columns[1:]
        if len(encoded_values) != len(present_keys):
            raise ValueError(f"record has {len(encoded_values)} values for {len(present_keys)} fields")

        record: dict[str, Any] = {}
        for key, encoded in zip(present_keys, encoded_values):
            if encoded == "=":
                if key not in previous_values:
                    raise ValueError(f"value reuse for {key!r} has no previous value")
                value = previous_values[key]
            elif encoded.startswith("^"):
                value = self._decode_timestamp(key, encoded, previous_timestamps=previous_timestamps)
            elif encoded.startswith("+"):
                if not isinstance(previous_values.get(key), int):
                    raise ValueError(f"integer delta for {key!r} has no previous value")
                value = int(previous_values[key]) + _base36_decode(encoded[1:])
            elif encoded.startswith("#"):
                value = _base36_decode(encoded[1:])
            elif encoded == "t":
                value = True
            elif encoded == "f":
                value = False
            elif encoded == "n":
                value = None
            elif encoded.startswith("&"):
                value = float(encoded[1:])
            else:
                decoded = self.decode_text(encoded)
                value = json.loads(decoded)
            if key == "__extra__":
                if isinstance(value, dict):
                    record.update(value)
                else:
                    record["__extra__"] = value
            else:
                record[key] = value

        for key in present_keys:
            if key == "__extra__":
                previous_values[key] = {extra_key: record[extra_key] for extra_key in record if extra_key not in set(self.schema) or extra_key == "__extra__"}
            else:
                previous_values[key] = record[key]
        return record


def _choose_base_time(records: Iterable[dict[str, Any]]) -> datetime:
    times = [_record_time(record) for record in records]
    valid = [stamp for stamp in times if stamp is not None]
    return min(valid) if valid else datetime.now(timezone.utc)


def encode_records(records: Iterable[dict[str, Any]], output: TextIO, *, base_time: datetime | None = None) -> None:
    items = list(records)
    codec = LexCodec(base_time=base_time or _choose_base_time(items))
    for line in codec.header_lines():
        output.write(line + "\n")
    previous_values: dict[str, Any] = {}
    previous_timestamps: dict[str, datetime] = {}
    previous_mask: list[int | None] = [None]
    for record in items:
        output.write(
            codec.encode_record(
                record,
                previous_values=previous_values,
                previous_timestamps=previous_timestamps,
                previous_mask=previous_mask,
            )
            + "\n"
        )


def encode_jsonl_file(input_path: Path | str, output_path: Path | str, *, since_minutes: float | None = None) -> dict[str, Any]:
    source = Path(input_path)
    records = _line_records_since_minutes(source.read_text(encoding="utf-8").splitlines(), since_minutes)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        encode_records(records, handle)
    return {
        "ok": True,
        "input_path": str(source),
        "output_path": str(target),
        "records": len(records),
        "bytes": target.stat().st_size,
        "since_minutes": since_minutes,
    }


def _parse_lex_header(lines: list[str]) -> tuple[LexCodec, int]:
    schema = DEFAULT_SCHEMA
    lex_by_id: dict[int, str] = {}
    protected_terms = PROTECTED_TERMS
    base_time: datetime | None = None
    records_index = -1

    for index, raw in enumerate(lines):
        line = raw.rstrip("\n")
        if line == "!records":
            records_index = index + 1
            break
        if line.startswith("!base-time "):
            parsed = parse_log_time(line.split(" ", 1)[1].strip())
            if parsed is not None:
                base_time = parsed
        elif line.startswith("!schema "):
            parsed_schema = json.loads(line.split(" ", 1)[1])
            if isinstance(parsed_schema, list) and all(isinstance(item, str) for item in parsed_schema):
                schema = tuple(parsed_schema)
        elif line.startswith("!protect "):
            terms = []
            for match in re.finditer(r'"(?:\\.|[^"])*"', line):
                value = json.loads(match.group(0))
                if isinstance(value, str):
                    terms.append(value)
            protected_terms = tuple(terms or protected_terms)
        elif line.startswith("!lex "):
            _, code, payload = line.split(" ", 2)
            lex_by_id[_base36_decode(code)] = json.loads(payload)

    if records_index < 0:
        raise ValueError("lex log is missing !records marker")
    if not lex_by_id:
        lex_strings = LEX_STRINGS
    else:
        lex_strings = tuple(lex_by_id[index] for index in sorted(lex_by_id))
    return LexCodec(schema=schema, lex_strings=lex_strings, protected_terms=protected_terms, base_time=base_time), records_index


def iter_lex_records(input_path: Path | str) -> Iterator[dict[str, Any]]:
    """Yield decoded records from one or more self-contained lex segments."""

    lines = Path(input_path).read_text(encoding="utf-8").splitlines()
    index = 0
    total = len(lines)
    while index < total:
        while index < total and not lines[index].startswith("!mclog-lex "):
            index += 1
        if index >= total:
            break

        segment_lines: list[str] = []
        while index < total:
            line = lines[index]
            if segment_lines and line.startswith("!mclog-lex "):
                break
            segment_lines.append(line)
            index += 1
        codec, records_index = _parse_lex_header(segment_lines)
        previous_values: dict[str, Any] = {}
        previous_timestamps: dict[str, datetime] = {}
        previous_mask: list[int | None] = [None]
        for line in segment_lines[records_index:]:
            if not line or line.startswith("#") or line == "!segment":
                continue
            if not line.startswith(_RECORD_PREFIX):
                continue
            yield codec.decode_record_line(
                line,
                previous_values=previous_values,
                previous_timestamps=previous_timestamps,
                previous_mask=previous_mask,
            )


def decode_lex_file(input_path: Path | str, output: TextIO, *, since_minutes: float | None = None) -> dict[str, Any]:
    records = list(iter_lex_records(input_path))
    records = filter_records_since_minutes(records, since_minutes)
    for record in records:
        output.write(canonical_json_line(record) + "\n")
    return {"ok": True, "input_path": str(input_path), "records": len(records), "since_minutes": since_minutes}


def inspect_lex_file(input_path: Path | str, *, since_minutes: float | None = None) -> dict[str, Any]:
    path = Path(input_path)
    records = list(iter_lex_records(path))
    filtered = filter_records_since_minutes(records, since_minutes)
    times = [_record_time(record) for record in filtered]
    valid = [stamp for stamp in times if stamp is not None]
    return {
        "ok": True,
        "path": str(path),
        "bytes": path.stat().st_size,
        "records": len(filtered),
        "records_total": len(records),
        "since_minutes": since_minutes,
        "first_at": format_log_time(min(valid)) if valid else "",
        "last_at": format_log_time(max(valid)) if valid else "",
    }


class LexLogWriter:
    """Append records to the plain-text reversible !lex log format."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._handle: TextIO | None = None
        self._codec: LexCodec | None = None
        self._previous_values: dict[str, Any] = {}
        self._previous_timestamps: dict[str, datetime] = {}
        self._previous_mask: list[int | None] = [None]
        self._header_written = False

    def __enter__(self) -> "LexLogWriter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _ensure_open(self, first_record: dict[str, Any]) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        exists = self.path.exists() and self.path.stat().st_size > 0
        mode = "a" if exists else "w"
        self._handle = self.path.open(mode, encoding="utf-8", newline="\n")
        if exists:
            # Appending to an existing lex stream is safe for human inspection.
            # The service starts fresh writers per process generation; if an
            # existing file is present, write a restart boundary with a new
            # self-contained header so decoding can continue from this point.
            self._handle.write("\n!segment\n")
        base_time = _record_time(first_record) or datetime.now(timezone.utc)
        self._codec = LexCodec(base_time=base_time)
        for line in self._codec.header_lines():
            self._handle.write(line + "\n")
        self._header_written = True

    def write_record(self, record: dict[str, Any]) -> None:
        self._ensure_open(record)
        assert self._handle is not None
        assert self._codec is not None
        self._handle.write(
            self._codec.encode_record(
                record,
                previous_values=self._previous_values,
                previous_timestamps=self._previous_timestamps,
                previous_mask=self._previous_mask,
            )
            + "\n"
        )
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            self._handle.close()
            self._handle = None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Encode/decode Main Computer plain-text !lex logs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    encode = subparsers.add_parser("encode", help="Encode JSONL into the text !lex format.")
    encode.add_argument("input", help="Input raw JSONL log path.")
    encode.add_argument("output", help="Output .lex log path.")
    encode.add_argument("--since-minutes", type=float, default=None, help="Only include records within N minutes of the newest timestamp in the input.")

    decode = subparsers.add_parser("decode", help="Decode a text !lex log back to raw JSONL.")
    decode.add_argument("input", help="Input .lex log path.")
    decode.add_argument("output", nargs="?", help="Output JSONL path. Defaults to stdout.")
    decode.add_argument("--since-minutes", type=float, default=None, help="Only output records within N minutes of the newest timestamp in the input.")

    inspect = subparsers.add_parser("inspect", help="Summarize a text !lex log.")
    inspect.add_argument("input", help="Input .lex log path.")
    inspect.add_argument("--since-minutes", type=float, default=None, help="Summarize records within N minutes of the newest timestamp in the input.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "encode":
        result = encode_jsonl_file(args.input, args.output, since_minutes=args.since_minutes)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "decode":
        if args.output:
            with Path(args.output).open("w", encoding="utf-8", newline="\n") as handle:
                decode_lex_file(args.input, handle, since_minutes=args.since_minutes)
        else:
            decode_lex_file(args.input, sys.stdout, since_minutes=args.since_minutes)
        return 0
    if args.command == "inspect":
        print(json.dumps(inspect_lex_file(args.input, since_minutes=args.since_minutes), indent=2, sort_keys=True))
        return 0
    raise AssertionError(f"unknown command: {args.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

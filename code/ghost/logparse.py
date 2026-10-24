"""Tolerant parser for the many log dialects found under dataset/evidence/logs."""
import json
import re

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(.*)$")
KV_RE = re.compile(r'([A-Za-z_]\w*)=("(?:[^"\\]|\\.)*"|[^\s"]\S*)')
BRACKET_RE = re.compile(r"^\[(\w+)\]\s+\[(\w+)\]\s+\[([\w-]+)\]\s+(\w+):\s*(.*)$")
PIPE_SEP = " | "
SYSLOG_RE = re.compile(r"^<\d+>1\s+\S+\s+(\w+)\s+(tr-\S+)\s+-\s+-\s+(.*)$")


def _unq(v):
    if v.startswith('"') and v.endswith('"'):
        v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def _kv(text):
    return {k: _unq(v) for k, v in KV_RE.findall(text)}


def parse_line(line):
    """Return (timestamp, dict) or None. Normalised keys: service, trace, event, level plus raw fields."""
    line = line.rstrip("\n")
    m = TS_RE.match(line)
    if not m:
        return None
    ts, rest = m.group(1), m.group(2).strip()
    if rest.startswith("{"):
        try:
            d = {k: ("" if v is None else str(v)) for k, v in json.loads(rest).items()}
        except ValueError:
            return None
        d["service"] = d.get("service") or d.get("logger", "")
        d["trace"] = d.get("trace_id", "")
        d["level"] = d.get("severity", "")
        return ts, d
    bm = BRACKET_RE.match(rest)
    if bm:
        d = _kv(bm.group(5))
        d.update(level=bm.group(1), service=bm.group(2), trace=bm.group(3), event=bm.group(4))
        return ts, d
    if PIPE_SEP in rest and not rest.startswith("level="):
        parts = rest.split(PIPE_SEP, 4)
        if len(parts) >= 4:
            d = _kv(parts[4]) if len(parts) > 4 else {}
            d.update(level=parts[0].strip(), service=parts[1].strip(), trace=parts[2].strip(), event=parts[3].strip())
            return ts, d
    sm = SYSLOG_RE.match(rest)
    if sm:
        d = _kv(sm.group(3))
        d.update(service=sm.group(1), trace=sm.group(2))
        return ts, d
    d = _kv(rest)
    if "message" in d and "requestId" in d:
        inner = _kv(d.pop("message"))
        d.update(inner)
        d["trace"] = d.pop("requestId")
        d["service"] = d.pop("function", d.get("service", ""))
        return ts, d
    d["trace"] = d.get("trace_id", d.get("trace", ""))
    return ts, d

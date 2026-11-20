"""Validate the Turkish prompt set, grade responses by rule, export for rubric review.

    python turkish_eval.py check prompts.jsonl
    python turkish_eval.py stats prompts.jsonl
    python turkish_eval.py autograde prompts.jsonl responses.jsonl
    python turkish_eval.py export prompts.jsonl responses.jsonl dataset.jsonl
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

CATEGORIES = (
    "ek_uyumu", "buyuk_kucuk_harf", "yazim", "ascii_donusum", "talimat", "bilgi",
    "problem", "mantik", "gramer", "ceviri", "deyim", "resmi_dil",
)
AUTO_KINDS = (
    "contains", "not_contains", "regex", "not_regex", "fullmatch", "order",
    "sentences", "max_words", "json_equals", "line_initials",
)
MAX_RESPONSE_CHARS = 200_000
MAX_WEIGHT = 1_000_000
COMBINING_DOT = "\u0307"
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "´": "'", "`": "'"})


def tr_upper(text):
    # str.upper() would turn i into I
    text = unicodedata.normalize("NFC", text).replace("i" + COMBINING_DOT, "i")
    return text.replace("i", "İ").replace("ı", "I").upper()


def tr_lower(text):
    # str.lower() turns İ into i + U+0307. NFC first so a decomposed İ is caught too.
    text = unicodedata.normalize("NFC", text)
    return text.replace("İ", "i").replace("I", "ı").lower()


def normalize(text):
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return text.translate(_APOSTROPHES)


def count_sentences(text):
    # Rough. Skips the dot in 15.00 or 3,5 but not in abbreviations like "vb."
    text = re.sub(r"(?<=\d)[.,](?=\d)", "", text.strip())
    parts = re.split(r"[.!?…]+(?:\s+|$)", text)
    return len([p for p in parts if p.strip()])


def count_words(text):
    return len(text.split())


def _strip_fence(text):
    text = text.strip()
    match = re.fullmatch(r"```(?:json)?[ \t]*\n(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _same_json(a, b):
    # Plain == says True == 1. We want 1 == 1.0 but not True == 1.
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_json(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same_json(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def run_check(check, response):
    kind, value = check["kind"], check["value"]
    text = normalize(response)
    if kind == "contains":
        return normalize(value) in text
    if kind == "not_contains":
        return normalize(value) not in text
    if kind == "regex":
        return re.search(value, text) is not None
    if kind == "fullmatch":
        return re.fullmatch(value, text.strip()) is not None
    if kind == "not_regex":
        return re.search(value, text) is None
    if kind == "order":
        position = 0
        for word in value:
            found = text.find(normalize(word), position)
            if found < 0:
                return False
            position = found + len(word)
        return True
    if kind == "sentences":
        return count_sentences(text) == value
    if kind == "max_words":
        return count_words(text) <= value
    if kind == "json_equals":
        try:
            parsed = json.loads(_strip_fence(response))
        except (ValueError, RecursionError):
            return False
        return _same_json(parsed, value)
    if kind == "line_initials":
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return len(lines) == len(value) and all(tr_upper(line[0]) == want for line, want in zip(lines, value))
    raise ValueError("unknown check kind: " + repr(kind))


def autograde_item(item, response):
    """Return (passed, details). passed is None when the item has no automatic checks."""
    checks = item.get("auto")
    if not checks:
        return None, []
    if not response.strip():
        return False, [("empty response", False)]
    if len(response) > MAX_RESPONSE_CHARS:
        return False, [("response too long", False)]
    details = []
    for check in checks:
        label = check["kind"] + ": " + (json.dumps(check["value"], ensure_ascii=False)
                                         if not isinstance(check["value"], str) else check["value"])
        details.append((label, run_check(check, response)))
    return all(ok for _, ok in details), details


def _read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except (ValueError, RecursionError) as error:
                raise ValueError("%s line %d: not valid JSON (%s)" % (path, number, error))
    return rows


def _has_surrogate(text):
    return any("\ud800" <= ch <= "\udfff" for ch in text)


def validate_prompts(items):
    problems = []
    seen = set()
    for i, item in enumerate(items, 1):
        where = "item %d" % i
        if not isinstance(item, dict):
            problems.append(where + ": must be an object")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item_id):
            problems.append(where + ": 'id' must be lower-case words joined by hyphens")
        elif item_id in seen:
            problems.append("%s: id '%s' is used twice" % (where, item_id))
        else:
            seen.add(item_id)
            where = item_id
        if item.get("category") not in CATEGORIES:
            problems.append("%s: 'category' must be one of %s" % (where, ", ".join(CATEGORIES)))
        difficulty = item.get("difficulty")
        if isinstance(difficulty, bool) or difficulty not in (1, 2, 3):
            problems.append(where + ": 'difficulty' must be 1, 2 or 3")
        for key in ("prompt", "reference"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                problems.append("%s: '%s' must be a non-empty string" % (where, key))
                continue
            if _has_surrogate(value):
                problems.append("%s: '%s' contains a broken character" % (where, key))
            elif unicodedata.normalize("NFC", value) != value:
                problems.append("%s: '%s' is not in NFC form" % (where, key))
            if COMBINING_DOT in value:
                problems.append("%s: '%s' contains a combining dot (U+0307), usually a bad İ/i conversion" % (where, key))
        problems.extend(_validate_criteria(where, item.get("criteria")))
        problems.extend(_validate_auto(where, item.get("auto")))
        if not problems_for(where, problems) and item.get("auto"):
            passed, details = autograde_item(item, item["reference"])
            if passed is False:
                failed = [label for label, ok in details if not ok]
                problems.append("%s: the reference answer fails its own automatic checks: %s" % (where, "; ".join(failed)))
    return problems


def problems_for(where, problems):
    return [p for p in problems if p.startswith(where + ":") or p.startswith(where + "/")]


def _validate_criteria(where, criteria):
    problems = []
    if not isinstance(criteria, list) or not criteria:
        return [where + ": needs at least one criterion"]
    ids = set()
    positive = 0
    for criterion in criteria:
        cid = criterion.get("id") if isinstance(criterion, dict) else None
        if not isinstance(cid, str) or not cid:
            problems.append(where + ": every criterion needs an 'id'")
            continue
        if cid in ids:
            problems.append("%s: criterion '%s' is used twice" % (where, cid))
        ids.add(cid)
        text = criterion.get("text")
        if not isinstance(text, str) or not text.strip():
            problems.append("%s/%s: 'text' must be a non-empty string" % (where, cid))
        elif unicodedata.normalize("NFC", text) != text or COMBINING_DOT in text:
            problems.append("%s/%s: 'text' is not clean NFC text" % (where, cid))
        weight = criterion.get("weight")
        if (isinstance(weight, bool) or not isinstance(weight, (int, float))
                or weight == 0 or not abs(weight) <= MAX_WEIGHT):
            problems.append("%s/%s: 'weight' must be a non-zero number" % (where, cid))
        elif weight > 0:
            positive += weight
    if positive == 0:
        problems.append(where + ": needs at least one criterion with a positive weight")
    return problems


def _validate_auto(where, auto):
    if auto is None:
        return []
    if not isinstance(auto, list) or not auto:
        return [where + ": 'auto' must be a non-empty list when present"]
    problems = []
    for index, check in enumerate(auto, 1):
        label = "%s: auto check %d" % (where, index)
        if not isinstance(check, dict) or check.get("kind") not in AUTO_KINDS or "value" not in check:
            problems.append(label + " needs 'kind' (one of %s) and 'value'" % ", ".join(AUTO_KINDS))
            continue
        kind, value = check["kind"], check["value"]
        if kind in ("contains", "not_contains") and not (isinstance(value, str) and value):
            problems.append(label + ": value must be a non-empty string")
        elif kind in ("regex", "not_regex", "fullmatch"):
            if not isinstance(value, str):
                problems.append(label + ": value must be a string")
            else:
                try:
                    re.compile(value)
                except re.error as error:
                    problems.append("%s: bad regular expression (%s)" % (label, error))
        elif kind in ("order", "line_initials") and not (
                isinstance(value, list) and value and all(isinstance(v, str) and v for v in value)):
            problems.append(label + ": value must be a non-empty list of strings")
        elif kind in ("sentences", "max_words") and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1):
            problems.append(label + ": value must be a positive integer")
    return problems


def load_prompts(path):
    items = _read_jsonl(path)
    problems = validate_prompts(items)
    if problems:
        raise ValueError("\n".join(problems))
    return items


def load_responses(path):
    """Return {id: response text}. Later lines for the same id replace earlier ones."""
    responses = {}
    for number, row in enumerate(_read_jsonl(path), 1):
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not isinstance(row.get("response"), str)):
            raise ValueError("%s: entry %d needs string fields 'id' and 'response'" % (path, number))
        responses[row["id"]] = row["response"]
    return responses


def cmd_check(args):
    try:
        items = _read_jsonl(args.prompts)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    problems = validate_prompts(items)
    for problem in problems:
        print(problem)
    if problems:
        print("%d problem(s) in %d item(s)" % (len(problems), len(items)))
        return 1
    print("ok: %d items" % len(items))
    return 0


def cmd_stats(args):
    try:
        items = load_prompts(args.prompts)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    by_category = defaultdict(Counter)
    for item in items:
        by_category[item["category"]][item["difficulty"]] += 1
    print("%-18s %5s %5s %5s %5s %6s" % ("category", "d1", "d2", "d3", "total", "auto"))
    for category in CATEGORIES:
        counts = by_category.get(category, Counter())
        total = sum(counts.values())
        auto = sum(1 for i in items if i["category"] == category and i.get("auto"))
        print("%-18s %5d %5d %5d %5d %6d" % (category, counts[1], counts[2], counts[3], total, auto))
    print("%-18s %5s %5s %5s %5d %6d" % ("all", "", "", "", len(items), sum(1 for i in items if i.get("auto"))))
    return 0


def _load_both(args):
    items = load_prompts(args.prompts)
    responses = load_responses(args.responses)
    known = {item["id"] for item in items}
    unknown = sorted(set(responses) - known)
    if unknown:
        print("note: ignoring responses for unknown ids: %s" % ", ".join(unknown[:10]), file=sys.stderr)
    return items, responses


def cmd_autograde(args):
    try:
        items, responses = _load_both(args)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    totals = defaultdict(lambda: [0, 0])
    missing = []
    manual = 0
    for item in items:
        response = responses.get(item["id"])
        if response is None:
            missing.append(item["id"])
            continue
        passed, details = autograde_item(item, response)
        if passed is None:
            manual += 1
            continue
        totals[item["category"]][1] += 1
        if passed:
            totals[item["category"]][0] += 1
        else:
            failed = [label for label, ok in details if not ok]
            print("FAIL %-22s %s" % (item["id"], "; ".join(failed)))
    print()
    print("%-18s %s" % ("category", "passed"))
    passed_all = graded_all = 0
    for category in CATEGORIES:
        if category in totals:
            p, n = totals[category]
            passed_all += p
            graded_all += n
            print("%-18s %d/%d" % (category, p, n))
    rate = "%.1f%%" % (100.0 * passed_all / graded_all) if graded_all else "n/a"
    print("%-18s %d/%d (%s)" % ("all", passed_all, graded_all, rate))
    print("%d item(s) have no automatic check and need a rubric review (use export)" % manual)
    if missing:
        print("%d item(s) have no response: %s" % (len(missing), ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else "")))
    return 0 if graded_all and passed_all == graded_all else 1


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


def cmd_export(args):
    try:
        items, responses = _load_both(args)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    rows = []
    skipped = []
    for item in items:
        response = responses.get(item["id"])
        if response is None or not response.strip():
            skipped.append(item["id"])
            continue
        rows.append({"id": item["id"], "prompt": item["prompt"], "response": response,
                     "criteria": item["criteria"]})
    if not rows:
        print("error: no item has a response, nothing to export", file=sys.stderr)
        return 2
    if any(_same_file(args.output, other) for other in (args.prompts, args.responses)):
        print("error: the output file must be a new file, not one of the inputs", file=sys.stderr)
        return 2
    try:
        data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
        with open(args.output, "wb") as handle:
            handle.write(data)
    except (OSError, UnicodeEncodeError) as error:
        print("error: cannot write %s (%s)" % (args.output, error), file=sys.stderr)
        return 2
    print("wrote %d item(s) to %s" % (len(rows), args.output))
    if skipped:
        print("skipped %d item(s) without a response" % len(skipped))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="validate the prompt file")
    p.add_argument("prompts")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("stats", help="count items per category and difficulty")
    p.add_argument("prompts")
    p.set_defaults(func=cmd_stats)
    p = sub.add_parser("autograde", help="grade responses with the rule-based checks")
    p.add_argument("prompts")
    p.add_argument("responses")
    p.set_defaults(func=cmd_autograde)
    p = sub.add_parser("export", help="write a rubric-eval dataset from prompts and responses")
    p.add_argument("prompts")
    p.add_argument("responses")
    p.add_argument("output")
    p.set_defaults(func=cmd_export)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

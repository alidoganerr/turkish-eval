import contextlib
import copy
import io
import json
import os
import tempfile
import unittest

import turkish_eval as te

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS = os.path.join(HERE, "prompts.jsonl")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = te.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_items():
    return te._read_jsonl(PROMPTS)


class TurkishText(unittest.TestCase):
    def test_upper(self):
        self.assertEqual(te.tr_upper("ılık iğne ışık istanbul"), "ILIK İĞNE IŞIK İSTANBUL")

    def test_lower(self):
        self.assertEqual(te.tr_lower("ISPARTA İZMİR IĞDIR"), "ısparta izmir ığdır")
        self.assertNotIn("̇", te.tr_lower("İSTANBUL"))

    def test_round_trip_of_dotted_and_dotless(self):
        self.assertEqual(te.tr_lower(te.tr_upper("ığdır diyarbakır")), "ığdır diyarbakır")

    def test_normalize_apostrophes_and_composed_forms(self):
        self.assertEqual(te.normalize("Kars’ta"), "Kars'ta")
        self.assertEqual(te.normalize("i\u0307"), "i\u0307")  # no composed form exists, stays as is
        self.assertEqual(te.normalize("Ç"), "Ç")

    def test_sentences(self):
        self.assertEqual(te.count_sentences("Bir. İki! Üç?"), 3)
        self.assertEqual(te.count_sentences("Toplantı saat 15.00'te. Gelin."), 2)
        self.assertEqual(te.count_sentences("Tek cümle"), 1)
        self.assertEqual(te.count_sentences("Ne oldu?!"), 1)
        self.assertEqual(te.count_sentences(""), 0)

    def test_words(self):
        self.assertEqual(te.count_words("bir iki  üç\nüç"), 4)

    def test_decomposed_input(self):
        self.assertEqual(te.tr_lower("I\u0307STANBUL"), "istanbul")
        self.assertEqual(te.tr_upper("i\u0307stanbul"), "İSTANBUL")
        self.assertEqual(te.tr_lower("\u0130Z\u0130R"), "izir")

    def test_crlf_is_normalised(self):
        self.assertEqual(te.normalize("a\r\nb\rc"), "a\nb\nc")


class Checks(unittest.TestCase):
    def check(self, kind, value, response):
        return te.run_check({"kind": kind, "value": value}, response)

    def test_contains_uses_apostrophe_normalisation(self):
        self.assertTrue(self.check("contains", "Kars'ta", "Kars’ta"))
        self.assertFalse(self.check("contains", "Kars'ta", "Karsta"))

    def test_not_contains(self):
        self.assertTrue(self.check("not_contains", "bir bin", "bin iki yüz"))
        self.assertFalse(self.check("not_contains", "bir bin", "bir bin iki yüz"))

    def test_regex_and_fullmatch(self):
        self.assertTrue(self.check("regex", r"\b81\b", "81 il var"))
        self.assertFalse(self.check("regex", r"\b81\b", "810 il var"))
        self.assertTrue(self.check("fullmatch", r"Evet\.?", " Evet. "))
        self.assertFalse(self.check("fullmatch", r"Evet\.?", "Evet, öyle"))

    def test_order(self):
        self.assertTrue(self.check("order", ["a", "b", "c"], "a, b, c"))
        self.assertFalse(self.check("order", ["a", "b", "c"], "a, c, b"))
        self.assertFalse(self.check("order", ["a", "b"], "a"))

    def test_order_does_not_reuse_the_same_text(self):
        self.assertFalse(self.check("order", ["gül", "gül"], "gül"))

    def test_json_equals(self):
        want = {"ad": "Ali", "yas": 25}
        self.assertTrue(self.check("json_equals", want, '{"yas": 25, "ad": "Ali"}'))
        self.assertTrue(self.check("json_equals", want, '```json\n{"ad": "Ali", "yas": 25}\n```'))
        self.assertFalse(self.check("json_equals", want, '{"ad": "Ali", "yas": "25"}'))
        self.assertFalse(self.check("json_equals", want, 'Tabii: {"ad": "Ali", "yas": 25}'))
        self.assertFalse(self.check("json_equals", {"ok": 1}, '{"ok": true}'))
        self.assertFalse(self.check("json_equals", want, "[" * 100000))

    def test_line_initials_with_dotted_capital_i(self):
        want = ["K", "İ", "T"]
        self.assertTrue(self.check("line_initials", want, "kuş\nincir\ntaş"))
        self.assertFalse(self.check("line_initials", want, "Kuş\nIncir\nTaş"))
        self.assertFalse(self.check("line_initials", want, "Kuş\nİncir"))

    def test_sentences_and_max_words(self):
        self.assertTrue(self.check("sentences", 1, "Tek cümle burada."))
        self.assertFalse(self.check("sentences", 1, "Bir. İki."))
        self.assertTrue(self.check("max_words", 3, "bir iki üç"))
        self.assertFalse(self.check("max_words", 3, "bir iki üç dört"))

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            self.check("magic", 1, "x")


class ShippedSet(unittest.TestCase):
    def test_validates(self):
        self.assertEqual(te.validate_prompts(load_items()), [])

    def test_size_and_spread(self):
        items = load_items()
        self.assertGreaterEqual(len(items), 50)
        self.assertEqual({i["category"] for i in items}, set(te.CATEGORIES))
        self.assertEqual({i["difficulty"] for i in items}, {1, 2, 3})

    def test_references_pass_their_own_checks(self):
        for item in load_items():
            if item.get("auto"):
                passed, details = te.autograde_item(item, item["reference"])
                self.assertTrue(passed, (item["id"], details))

    def test_empty_response_fails_every_automatic_item(self):
        for item in load_items():
            if item.get("auto"):
                passed, _ = te.autograde_item(item, "")
                self.assertFalse(passed, item["id"])

    def test_known_wrong_answers_fail(self):
        wrong = {
            "ek-cogul": "kalplar, saatlar, alkollar, rollar, harflar",
            "harf-buyuk": "ILIK, IĞNE, ISIK, ISTANBUL, ISPANAK",
            "harf-siralama": "ağaç, çay, göl, gül, iğne, ılık, ördek, şeker, zeytin",
            "yazim-sayi": "bir bin iki yüz elli; iki bin beş; on bin üç yüz",
            "problem-indirim": "360 * 0,65 = 234 TL",
            "mantik-makine": "100 dakika",
            "bilgi-agri": "4.000 metre",
            "talimat-evet-hayir": "Evet, çünkü yüksek bir dağdır.",
            "talimat-json": '{"ad": "Ali", "yas": "25"}',
        }
        by_id = {i["id"]: i for i in load_items()}
        for item_id, response in wrong.items():
            passed, _ = te.autograde_item(by_id[item_id], response)
            self.assertFalse(passed, item_id)

    def test_alphabet_order_used_by_the_sorting_item(self):
        alphabet = "abcçdefgğhıijklmnoöprsştuüvyz"
        words = ["çay", "ağaç", "şeker", "göl", "ılık", "iğne", "ördek", "zeytin", "gül"]
        ordered = sorted(words, key=lambda w: [alphabet.index(c) for c in w])
        item = {i["id"]: i for i in load_items()}["harf-siralama"]
        self.assertEqual(item["auto"][0]["value"], ordered)

    def test_prompt_numbers_agree_with_arithmetic(self):
        # independent recomputation of the numeric answers
        self.assertEqual(225 / 3 * 5, 375)
        self.assertAlmostEqual(360 * 0.75 * 1.10, 297)
        self.assertEqual(2 * (7 + 77 // 7), 36)
        self.assertAlmostEqual(90 * (2 + 20 / 60), 210)
        self.assertAlmostEqual(960 * 1.2, 1152)
        from datetime import date, timedelta
        self.assertEqual(date(2026, 11, 19) + timedelta(days=100), date(2027, 2, 27))
        self.assertEqual(date(2027, 2, 27).strftime("%A"), "Saturday")

    def test_wording_variants_that_should_pass(self):
        by_id = {i["id"]: i for i in load_items()}
        good = {
            "mantik-makine": ["5 dakikada", "Beş dakika", "5 dk"],
            "yazim-sayi": ["Bin iki yüz elli; iki bin beş; on bin üç yüz"],
            "talimat-evet-hayir": ["evet", "Evet."],
            "bilgi-cumhuriyet": ["29.10.1923", "29 Ekim 1923'te"],
            "bilgi-agri": ["5,137 m", "5.137 metre"],
            "mantik-top": ["3 / 28", "Olasılık 3/28'dir."],
            "ek-soru-eki": ["Bu kitap güzel mi? Yarın geliyor musun?"],
            "gramer-zaman": ["mişli geçmiş zaman ve gelecek zaman"],
            "talimat-k-yok": ["elbise\r\nbahçeli\r\nmasalcı\r\nyağmurlu\r\ndenizci"],
        }
        for item_id, responses in good.items():
            for response in responses:
                passed, details = te.autograde_item(by_id[item_id], response)
                self.assertTrue(passed, (item_id, response, details))

    def test_more_wrong_answers_fail(self):
        by_id = {i["id"]: i for i in load_items()}
        bad = {
            "ek-fiil-cekimi": "yiyeceğim, diyorum, gitmeyiz",
            "mantik-top": "13/28",
            "mantik-boy": "En kısa Can'dır, Deniz değil.",
            "gramer-ogeler": "'Yağmurdan sonra çocuklar bahçeye koştu.' Bu cümlenin öznesi ve yüklemi hangisi?",
            "problem-yas": "Ali 20 yaşında, kardeşi 10 yaşında.",
        }
        for item_id, response in bad.items():
            passed, _ = te.autograde_item(by_id[item_id], response)
            self.assertFalse(passed, item_id)


class Validation(unittest.TestCase):
    def broken(self, mutate):
        items = copy.deepcopy(load_items())
        mutate(items)
        return te.validate_prompts(items)

    def test_duplicate_id(self):
        problems = self.broken(lambda items: items[1].__setitem__("id", items[0]["id"]))
        self.assertTrue(any("used twice" in p for p in problems))

    def test_bad_category_and_difficulty(self):
        def mutate(items):
            items[0]["category"] = "nope"
            items[1]["difficulty"] = 4
            items[2]["difficulty"] = True
        problems = self.broken(mutate)
        self.assertEqual(sum("category" in p for p in problems), 1)
        self.assertEqual(sum("difficulty" in p for p in problems), 2)

    def test_combining_dot_and_non_nfc(self):
        def mutate(items):
            items[0]["prompt"] = "i̇stanbul"
            items[1]["reference"] = "Çay"
        problems = self.broken(mutate)
        self.assertTrue(any("combining dot" in p for p in problems))
        self.assertTrue(any("NFC" in p for p in problems))

    def test_bad_weights(self):
        def mutate(items):
            items[0]["criteria"][0]["weight"] = 0
            items[1]["criteria"][0]["weight"] = float("nan")
            items[2]["criteria"] = [{"id": "x", "text": "t", "weight": -1}]
        problems = self.broken(mutate)
        self.assertGreaterEqual(len(problems), 3)

    def test_bad_regex_and_unknown_kind(self):
        def mutate(items):
            auto_items = [i for i in items if i.get("auto")]
            auto_items[0]["auto"] = [{"kind": "regex", "value": "("}]
            auto_items[1]["auto"] = [{"kind": "magic", "value": "x"}]
            auto_items[2]["auto"] = []
        problems = self.broken(mutate)
        self.assertTrue(any("bad regular expression" in p for p in problems))
        self.assertTrue(any("magic" in p or "kind" in p for p in problems))
        self.assertTrue(any("non-empty list" in p for p in problems))

    def test_reference_that_fails_its_checks_is_reported(self):
        def mutate(items):
            target = next(i for i in items if i["id"] == "bilgi-il")
            target["reference"] = "80"
        problems = self.broken(mutate)
        self.assertTrue(any("bilgi-il" in p and "reference answer fails" in p for p in problems))

    def test_garbage_items(self):
        problems = te.validate_prompts([1, None, {}, {"id": "A_B"}])
        self.assertGreaterEqual(len(problems), 4)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def write_responses(self, name, mapping):
        with open(self.path(name), "w", encoding="utf-8") as f:
            for key, value in mapping.items():
                f.write(json.dumps({"id": key, "response": value}, ensure_ascii=False) + "\n")
        return self.path(name)

    def test_check_and_stats(self):
        code, out, _ = run("check", PROMPTS)
        self.assertEqual(code, 0)
        self.assertIn("ok:", out)
        code, out, _ = run("stats", PROMPTS)
        self.assertEqual(code, 0)
        self.assertIn("ek_uyumu", out)

    def test_check_reports_bad_file_cleanly(self):
        with open(self.path("bad.jsonl"), "w", encoding="utf-8") as f:
            f.write("{not json}\n")
        code, _, err = run("check", self.path("bad.jsonl"))
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)
        code, _, err = run("check", self.path("missing.jsonl"))
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_non_utf8_file(self):
        with open(self.path("latin.jsonl"), "wb") as f:
            f.write(b'{"id": "\xfc"}\n')
        code, _, err = run("check", self.path("latin.jsonl"))
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_autograde_all_references_pass(self):
        refs = {i["id"]: i["reference"] for i in load_items()}
        responses = self.write_responses("refs.jsonl", refs)
        code, out, _ = run("autograde", PROMPTS, responses)
        self.assertEqual(code, 0, out)
        self.assertIn("100.0%", out)
        self.assertIn("rubric review", out)

    def test_autograde_reports_failures_and_missing(self):
        responses = self.write_responses("r.jsonl", {"bilgi-il": "80", "mantik-boy": "Deniz", "nope": "x"})
        code, out, err = run("autograde", PROMPTS, responses)
        self.assertEqual(code, 1)
        self.assertIn("FAIL bilgi-il", out)
        self.assertIn("unknown ids", err)
        self.assertIn("have no response", out)

    def test_autograde_rejects_malformed_responses(self):
        with open(self.path("r.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"id": "a"}\n')
        code, _, err = run("autograde", PROMPTS, self.path("r.jsonl"))
        self.assertEqual(code, 2)
        self.assertIn("'id' and 'response'", err)

    def test_export_format(self):
        responses = self.write_responses("r.jsonl", {"bilgi-il": "81", "deyim-yorgan": "Gelire göre harca."})
        out_path = self.path("dataset.jsonl")
        code, out, _ = run("export", PROMPTS, responses, out_path)
        self.assertEqual(code, 0, out)
        rows = te._read_jsonl(out_path)
        self.assertEqual([r["id"] for r in rows], ["bilgi-il", "deyim-yorgan"])
        for row in rows:
            self.assertEqual(set(row), {"id", "prompt", "response", "criteria"})
            self.assertTrue(row["criteria"])
        with open(out_path, encoding="utf-8") as f:
            self.assertIn("Gelire göre harca.", f.read())  # readable UTF-8, not escapes

    def test_export_refuses_to_overwrite_inputs_and_empty_output(self):
        responses = self.write_responses("r.jsonl", {"bilgi-il": "81"})
        code, _, err = run("export", PROMPTS, responses, PROMPTS)
        self.assertEqual(code, 2)
        empty = self.write_responses("empty.jsonl", {"bilgi-il": "   "})
        code, _, err = run("export", PROMPTS, empty, self.path("o.jsonl"))
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.path("o.jsonl")))

    def test_export_unwritable_output(self):
        responses = self.write_responses("r.jsonl", {"bilgi-il": "81"})
        code, _, err = run("export", PROMPTS, responses, self.path("no_such_dir/o.jsonl"))
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_export_refuses_aliased_input_path(self):
        responses = self.write_responses("r.jsonl", {"bilgi-il": "81"})
        alias = os.path.join(self.tmp.name, ".", "sub", "..", "r.jsonl")
        os.makedirs(os.path.join(self.tmp.name, "sub"), exist_ok=True)
        before = read_text(responses)
        code, _, _ = run("export", PROMPTS, responses, alias)
        self.assertEqual(code, 2)
        self.assertEqual(read_text(responses), before)

    def test_export_with_lone_surrogate_leaves_no_file(self):
        with open(self.path("r.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"id": "bilgi-il", "response": "81 \\ud800"}\n')
        code, _, err = run("export", PROMPTS, self.path("r.jsonl"), self.path("o.jsonl"))
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.path("o.jsonl")))
        self.assertNotIn("Traceback", err)

    def test_byte_order_mark_is_accepted(self):
        with open(self.path("r.jsonl"), "w", encoding="utf-8-sig") as f:
            f.write(json.dumps({"id": "bilgi-il", "response": "81"}) + "\n")
        code, out, _ = run("autograde", PROMPTS, self.path("r.jsonl"))
        self.assertNotEqual(code, 2, out)

    def test_huge_response_does_not_hang(self):
        responses = self.write_responses("r.jsonl", {"talimat-k-yok": "a" * 500_000})
        code, out, _ = run("autograde", PROMPTS, responses)
        self.assertEqual(code, 1)
        self.assertIn("response too long", out)


if __name__ == "__main__":
    unittest.main()

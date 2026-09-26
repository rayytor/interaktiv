"""
The profile has to be a faithful description of the detector, not a second one.

Three ways it could quietly stop being that, and a test for each:

  - a default drifts from the literal the module ships, so a "default" bake is
    not the detector the source reads;
  - someone adds `from .regions import SOMETHING` and the profile writes only
    the original binding, so one knob runs at two values;
  - a constant is used as a function's default argument, which Python evaluates
    once at import, so that call site keeps the value the module was loaded
    with however many profiles are applied afterwards.

All three are read out of the source with `ast` rather than out of the imported
modules, because an imported module has already had a profile applied to it and
would happily agree with whatever was applied.
"""

import ast
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from tools.hotspot_extraction.scanner import profile as P

SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))


def _source_literals(module: str):
    """Every `NAME = <number>` assigned at module scope, straight from the file."""
    tree = ast.parse(open(os.path.join(SCANNER_DIR, f"{module}.py"), encoding="utf-8").read())
    out = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, (int, float))
                and not isinstance(node.value.value, bool)):
            out[node.targets[0].id] = node.value.value
    return out


def _module_level_imports(module: str):
    """`from .other import NAME` at module scope -- the bindings a profile must also write."""
    tree = ast.parse(open(os.path.join(SCANNER_DIR, f"{module}.py"), encoding="utf-8").read())
    out = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            for alias in node.names:
                out.append((node.module, alias.name, alias.asname))
    return out


class TestSpecMatchesSource(unittest.TestCase):

    def test_every_default_is_the_literal_the_module_ships(self):
        for knob in P.SPEC:
            literals = _source_literals(knob.module)
            self.assertIn(knob.name, literals,
                          f"{knob.key}: no module-level {knob.name} in {knob.module}.py")
            self.assertEqual(
                literals[knob.name], knob.default,
                f"{knob.key}: SPEC says {knob.default}, {knob.module}.py says {literals[knob.name]}",
            )

    def test_every_tunable_literal_has_a_knob(self):
        """A constant nobody declared is a constant the fitter can never reach."""
        declared = {(k.module, k.name) for k in P.SPEC}
        missing = []
        for module in P.TARGET_MODULES:
            for name, value in _source_literals(module).items():
                if not name.isupper() or name.startswith("_"):
                    continue
                if name.endswith("_VERSION"):
                    continue  # format versions, not thresholds
                if (module, name) not in declared:
                    missing.append(f"{module}.{name} = {value}")
        self.assertEqual([], missing, f"undeclared constants: {missing}")

    def test_binds_in_lists_every_module_that_imported_the_name(self):
        expected = {(k.module, k.name): {k.module} for k in P.SPEC}
        for module in P.TARGET_MODULES:
            for source, name, asname in _module_level_imports(module):
                key = (source, name)
                if key in expected:
                    self.assertIsNone(
                        asname,
                        f"{module}.py renames {source}.{name}; a profile cannot follow a rename",
                    )
                    expected[key].add(module)
        for knob in P.SPEC:
            self.assertEqual(
                expected[(knob.module, knob.name)], set(knob.targets),
                f"{knob.key}: binds_in is {sorted(knob.targets)}, the imports say "
                f"{sorted(expected[(knob.module, knob.name)])}",
            )

    def test_no_knob_is_a_function_default(self):
        """
        A default argument is evaluated once, at import.

        `cuts(rect, block, whole_tol=WHOLE_TOL)` and `_cluster_by_x(..., tol=COLUMN_TOLERANCE)`
        both used to be written that way, and both would have gone on using the
        import-time value after every `apply_profile`. The fix in each case was
        `None` plus a lookup in the body, and this test is what stops the
        pattern coming back.
        """
        knob_names = {k.name for k in P.SPEC}
        offenders = []
        for module in P.TARGET_MODULES + ("score",):
            tree = ast.parse(open(os.path.join(SCANNER_DIR, f"{module}.py"), encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
                for d in defaults:
                    for sub in ast.walk(d):
                        if isinstance(sub, ast.Name) and sub.id in knob_names:
                            offenders.append(f"{module}.{node.name}({sub.id}=...)")
        self.assertEqual([], offenders, f"knob frozen into a default argument: {offenders}")


class TestApplyProfile(unittest.TestCase):

    def setUp(self):
        self.addCleanup(P.apply_profile, P.DEFAULTS)

    def test_apply_reaches_every_binding_of_a_name(self):
        moved = P.DEFAULTS.replace(PAD=13.0, COLUMN_TOLERANCE=19.0, COLUMN_CHANNEL=17.0)
        P.apply_profile(moved)
        held = P.source_values()
        self.assertEqual(13.0, held["regions"]["PAD"])
        self.assertEqual(13.0, held["anchors"]["PAD"], "anchors.py imported PAD and kept the old one")
        self.assertEqual(19.0, held["layout"]["COLUMN_TOLERANCE"])
        self.assertEqual(19.0, held["markers"]["COLUMN_TOLERANCE"])
        self.assertEqual(19.0, held["prompts"]["COLUMN_TOLERANCE"])
        self.assertEqual(17.0, held["regions"]["COLUMN_CHANNEL"])

    def test_two_knobs_sharing_a_name_stay_apart(self):
        """`HEADING_RATIO` is 1.12 in prompts and 1.15 in regions, and must stay two knobs."""
        moved = P.DEFAULTS.replace(HEADING_RATIO__prompts=1.30)
        P.apply_profile(moved)
        held = P.source_values()
        self.assertEqual(1.30, held["prompts"]["HEADING_RATIO"])
        self.assertEqual(1.15, held["regions"]["HEADING_RATIO"])

    def test_applying_defaults_restores_the_source_values(self):
        P.apply_profile(P.DEFAULTS.replace(PAD=30.0))
        P.apply_profile(P.DEFAULTS)
        for knob in P.SPEC:
            held = P.source_values()
            for target in knob.targets:
                self.assertEqual(knob.default, held[target][knob.name], knob.key)

    def test_the_scorer_ruler_does_not_move(self):
        """
        Widening `WHOLE_TOL` would erase cut violations without moving an edge.

        The scorer must therefore read a frozen copy. If this ever fails, the
        objective has become tunable and every number downstream of it is
        meaningless.
        """
        from tools.hotspot_extraction.scanner import score
        before = (score.MIN_HOTSPOT, score.TALL_REGION, score.WHOLE_TOL)
        P.apply_profile(P.DEFAULTS.replace(PAD=30.0))
        self.assertEqual(before, (score.MIN_HOTSPOT, score.TALL_REGION, score.WHOLE_TOL))
        self.assertEqual(P.RULER.WHOLE_TOL, score.WHOLE_TOL)

    def test_the_ruler_knobs_are_not_tunable(self):
        tunable = {k.key for k in P.TUNABLE}
        for name in ("WHOLE_TOL", "TALL_REGION", "MIN_HOTSPOT"):
            self.assertNotIn(name, tunable, f"{name} is the scorer's own rule; it may not be fitted")


class TestSerialisation(unittest.TestCase):

    def test_hash_is_stable_and_distinguishes_profiles(self):
        self.assertEqual(P.profile_hash(P.DEFAULTS), P.profile_hash(P.Profile()))
        self.assertNotEqual(P.profile_hash(P.DEFAULTS), P.profile_hash(P.DEFAULTS.replace(PAD=8.5)))

    def test_round_trip_through_json(self):
        p = P.DEFAULTS.replace(PAD=9.5, GRAPHIC_PASSES=11, HEADING_RATIO__regions=1.21)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "profile.json")
            written = P.save_profile(p, path, note="unit test")
            self.assertEqual(P.profile_hash(p), written)
            back = P.load_profile(path)
            self.assertEqual(p, back)
            body = json.load(open(path, encoding="utf-8"))
            self.assertEqual({"PAD", "GRAPHIC_PASSES", "HEADING_RATIO__regions"}, set(body["knobs"]))

    def test_a_missing_profile_is_the_defaults(self):
        self.assertEqual(P.DEFAULTS, P.load_profile(os.path.join(tempfile.gettempdir(), "no-such-profile.json")))

    def test_a_tampered_hash_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "profile.json")
            P.save_profile(P.DEFAULTS.replace(PAD=9.5), path)
            body = json.load(open(path, encoding="utf-8"))
            body["knobs"]["PAD"] = 10.5
            json.dump(body, open(path, "w", encoding="utf-8"), indent=2)
            with self.assertRaises(ValueError):
                P.load_profile(path)

    def test_out_of_range_and_unknown_keys_are_refused(self):
        with self.assertRaises(ValueError):
            P.from_dict({"PAD": 1000.0})
        with self.assertRaises(KeyError):
            P.from_dict({"NOT_A_KNOB": 1.0})

    def test_integer_knobs_stay_integers(self):
        p = P.from_dict({"GRAPHIC_PASSES": 7.6})
        self.assertIsInstance(p.GRAPHIC_PASSES, int)
        self.assertEqual(8, p.GRAPHIC_PASSES)


if __name__ == "__main__":
    unittest.main()

import unittest

from rpgle import IndicatorCond, parse_dds

from _fixtures import read_adventure_dds


class TestParseDds(unittest.TestCase):
    def test_finds_all_record_formats(self):
        dds = parse_dds(read_adventure_dds())

        self.assertEqual(sorted(dds.formats.keys()), ["COMBAT", "ENDGAME", "MAIN"])
        self.assertEqual(dds.formats["MAIN"].keys["F3"], "03")
        self.assertEqual(dds.formats["COMBAT"].keys["F3"], "03")

        main_field_names = [f.name for f in dds.formats["MAIN"].fields if f.kind == "field"]
        self.assertEqual(main_field_names, ["LOCTXT", "PLRHP", "MSGTXT", "CMD"])

        cmd_field = next(f for f in dds.formats["MAIN"].fields if f.kind == "field" and f.name == "CMD")
        self.assertEqual(cmd_field.usage, "B")

        msg_field = next(f for f in dds.formats["MAIN"].fields if f.kind == "field" and f.name == "MSGTXT")
        self.assertEqual(msg_field.indicators, [IndicatorCond(not_=False, num="90")])


if __name__ == "__main__":
    unittest.main()

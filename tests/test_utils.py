import unittest

from pwhl_shot_tracking.utils import parse_clock


class ParseClockTests(unittest.TestCase):
    def test_classic_formats_are_unchanged(self):
        self.assertEqual(90, parse_clock("90"))
        self.assertEqual(1157, parse_clock("19:17"))
        self.assertEqual(3661, parse_clock("1:01:01"))
        self.assertEqual(17, parse_clock(17.9))

    def test_scorebug_tenths_formats_truncate_toward_zero(self):
        # Under a minute the broadcast scorebug shows SS.t, sometimes with a
        # leading colon; game 347 discovery produced 29 such anchors.
        self.assertEqual(52, parse_clock(":52.9"))
        self.assertEqual(17, parse_clock("17.9"))
        self.assertEqual(7, parse_clock("07.9"))
        self.assertEqual(45, parse_clock("0:45.5"))
        self.assertEqual(0, parse_clock("00.0"))
        self.assertEqual(63, parse_clock("1:03.4"))

    def test_invalid_values_still_raise(self):
        for value in ("", None, "ab:cd", "ab.cd", "1:2:3:4", ".9", "1:.9"):
            with self.assertRaises(ValueError):
                parse_clock(value)


if __name__ == "__main__":
    unittest.main()

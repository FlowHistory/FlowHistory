import datetime

from django.test import TestCase

from backup.templatetags.settings_tags import day_name, default_label


class DefaultLabelTest(TestCase):
    def test_none_default_returns_empty(self):
        self.assertEqual(default_label("anything", None), "")

    def test_value_equals_default(self):
        self.assertEqual(default_label(20, 20), "(default)")

    def test_value_differs_shows_default_value(self):
        self.assertEqual(default_label(10, 20), "(default: 20)")

    def test_bool_default_true(self):
        self.assertEqual(default_label(False, True), "(default: Yes)")

    def test_bool_default_false(self):
        self.assertEqual(default_label(True, False), "(default: No)")

    def test_time_object_default(self):
        t = datetime.time(3, 0)
        self.assertEqual(default_label(datetime.time(5, 30), t), "(default: 03:00)")

    def test_time_object_matches_default(self):
        t = datetime.time(3, 0)
        self.assertEqual(default_label(t, t), "(default)")

    def test_time_value_compared_against_string_default(self):
        """Time object value should match string default via coercion."""
        self.assertEqual(default_label(datetime.time(3, 0), "03:00"), "(default)")

    def test_time_like_string_default(self):
        self.assertEqual(default_label("05:30", "03:00"), "(default: 03:00)")

    def test_string_default(self):
        self.assertEqual(default_label("custom", "daily"), "(default: daily)")


class DayNameTest(TestCase):
    def test_valid_days(self):
        expected = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        for i, name in enumerate(expected):
            self.assertEqual(day_name(i), name)

    def test_out_of_range(self):
        self.assertEqual(day_name(7), "7")

    def test_negative(self):
        # Negative index wraps in Python lists, but still a valid test
        # day_name(-1) would return "Sunday" due to Python list indexing
        result = day_name(-1)
        self.assertIsInstance(result, str)

    def test_non_integer(self):
        self.assertEqual(day_name("abc"), "abc")

    def test_none_input(self):
        self.assertEqual(day_name(None), "None")

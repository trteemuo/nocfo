"""Unit tests for the matching logic.

Run with: python -m pytest test_match.py -v
Or: python test_match.py
"""

import unittest
from src.match import (
    normalize_reference,
    levenshtein_distance,
    names_match,
    amounts_match,
    dates_within_range,
    get_counterparty,
    get_attachment_dates,
    is_direction_compatible,
)


class TestNormalizeReference(unittest.TestCase):
    """Test reference number normalization."""

    def test_removes_whitespace(self):
        self.assertEqual(normalize_reference("1234 5678"), "12345678")
        self.assertEqual(normalize_reference("9876 543 2103"), "98765432103")

    def test_removes_leading_zeros(self):
        self.assertEqual(normalize_reference("00001234"), "1234")
        self.assertEqual(normalize_reference("0000 0000 5550 0011 14"), "5550001114")

    def test_preserves_rf_prefix(self):
        self.assertEqual(normalize_reference("RF135550001114"), "RF135550001114")
        self.assertEqual(normalize_reference("RF00012345"), "RF12345")

    def test_preserves_fi_prefix(self):
        self.assertEqual(normalize_reference("FI001234"), "FI1234")

    def test_handles_none(self):
        self.assertIsNone(normalize_reference(None))

    def test_handles_empty_string(self):
        self.assertIsNone(normalize_reference(""))
        self.assertIsNone(normalize_reference("   "))

    def test_all_zeros_becomes_zero(self):
        self.assertEqual(normalize_reference("0000"), "0")

    def test_no_modification_needed(self):
        self.assertEqual(normalize_reference("12345672"), "12345672")


class TestLevenshteinDistance(unittest.TestCase):
    """Test Levenshtein distance calculation."""

    def test_identical_strings(self):
        self.assertEqual(levenshtein_distance("hello", "hello"), 0)

    def test_one_character_difference(self):
        self.assertEqual(levenshtein_distance("hello", "hallo"), 1)

    def test_insertion(self):
        self.assertEqual(levenshtein_distance("cat", "cats"), 1)

    def test_deletion(self):
        self.assertEqual(levenshtein_distance("cats", "cat"), 1)

    def test_substitution(self):
        self.assertEqual(levenshtein_distance("cat", "bat"), 1)

    def test_multiple_edits(self):
        self.assertEqual(levenshtein_distance("kitten", "sitting"), 3)

    def test_finnish_names(self):
        # "Meikäläinen" vs "Meittiläinen" - 3 character differences
        self.assertEqual(levenshtein_distance("meikäläinen", "meittiläinen"), 3)

    def test_empty_strings(self):
        self.assertEqual(levenshtein_distance("", "hello"), 5)
        self.assertEqual(levenshtein_distance("hello", ""), 5)
        self.assertEqual(levenshtein_distance("", ""), 0)


class TestNamesMatch(unittest.TestCase):
    """Test name matching logic."""

    def test_exact_match(self):
        self.assertTrue(names_match("John Doe", "John Doe"))

    def test_case_insensitive(self):
        self.assertTrue(names_match("John Doe", "john doe"))
        self.assertTrue(names_match("JOHN DOE", "john doe"))

    def test_substring_match_with_suffix(self):
        self.assertTrue(names_match("Matti Meikäläinen", "Matti Meikäläinen Tmi"))
        self.assertTrue(names_match("Best Supplies", "Best Supplies EMEA"))

    def test_substring_match_bidirectional(self):
        self.assertTrue(names_match("John Doe Consulting", "John Doe"))
        self.assertTrue(names_match("John Doe", "John Doe Consulting"))

    def test_different_surnames_rejected(self):
        self.assertFalse(names_match("Matti Meikäläinen", "Matti Meittiläinen"))

    def test_completely_different_names(self):
        self.assertFalse(names_match("Jane Smith", "John Doe"))

    def test_fuzzy_match_single_char_diff(self):
        # "Meikäläinen" vs "Meikälöinen" - 1 character difference
        self.assertTrue(names_match("Meikäläinen", "Meikälöinen"))

    def test_none_values(self):
        self.assertFalse(names_match(None, "John Doe"))
        self.assertFalse(names_match("John Doe", None))
        self.assertFalse(names_match(None, None))

    def test_whitespace_handling(self):
        self.assertTrue(names_match("  John Doe  ", "John Doe"))


class TestAmountsMatch(unittest.TestCase):
    """Test amount matching logic."""

    def test_exact_match(self):
        self.assertTrue(amounts_match(100.0, 100.0))

    def test_negative_and_positive(self):
        # Transaction uses negative for outgoing, attachments always positive
        self.assertTrue(amounts_match(-100.0, 100.0))
        self.assertTrue(amounts_match(100.0, 100.0))

    def test_within_tolerance(self):
        self.assertTrue(amounts_match(100.0, 100.009))
        self.assertTrue(amounts_match(100.009, 100.0))

    def test_outside_tolerance(self):
        self.assertFalse(amounts_match(100.0, 100.02))
        self.assertFalse(amounts_match(100.0, 99.98))

    def test_zero_amounts(self):
        self.assertTrue(amounts_match(0.0, 0.0))


class TestDatesWithinRange(unittest.TestCase):
    """Test date range checking."""

    def test_exact_match(self):
        self.assertTrue(dates_within_range("2024-06-15", "2024-06-15"))

    def test_one_day_difference(self):
        self.assertTrue(dates_within_range("2024-06-15", "2024-06-16"))
        self.assertTrue(dates_within_range("2024-06-16", "2024-06-15"))

    def test_two_days_difference(self):
        self.assertFalse(dates_within_range("2024-06-15", "2024-06-17"))

    def test_custom_tolerance(self):
        self.assertTrue(dates_within_range("2024-06-15", "2024-06-18", days=3))
        self.assertFalse(dates_within_range("2024-06-15", "2024-06-19", days=3))

    def test_invalid_date_format(self):
        self.assertFalse(dates_within_range("invalid", "2024-06-15"))
        self.assertFalse(dates_within_range("2024-06-15", "invalid"))


class TestGetCounterparty(unittest.TestCase):
    """Test counterparty extraction from attachments."""

    def test_purchase_invoice_with_supplier(self):
        attachment = {
            "type": "invoice",
            "data": {"supplier": "Acme Corp", "total_amount": 100.0}
        }
        self.assertEqual(get_counterparty(attachment), "Acme Corp")

    def test_sales_invoice_with_recipient(self):
        attachment = {
            "type": "invoice",
            "data": {"recipient": "Customer Inc", "total_amount": 100.0}
        }
        self.assertEqual(get_counterparty(attachment), "Customer Inc")

    def test_receipt_with_supplier(self):
        attachment = {
            "type": "receipt",
            "data": {"supplier": "Store Name", "total_amount": 50.0}
        }
        self.assertEqual(get_counterparty(attachment), "Store Name")

    def test_no_counterparty(self):
        attachment = {"type": "invoice", "data": {"total_amount": 100.0}}
        self.assertIsNone(get_counterparty(attachment))

    def test_supplier_takes_precedence(self):
        # If both supplier and recipient exist, supplier is returned first
        attachment = {
            "type": "invoice",
            "data": {"supplier": "Supplier", "recipient": "Recipient"}
        }
        self.assertEqual(get_counterparty(attachment), "Supplier")


class TestGetAttachmentDates(unittest.TestCase):
    """Test date extraction from attachments."""

    def test_invoice_with_both_dates(self):
        attachment = {
            "type": "invoice",
            "data": {
                "invoicing_date": "2024-06-15",
                "due_date": "2024-07-15"
            }
        }
        dates = get_attachment_dates(attachment)
        self.assertEqual(len(dates), 2)
        self.assertIn("2024-06-15", dates)
        self.assertIn("2024-07-15", dates)

    def test_receipt_with_receiving_date(self):
        attachment = {
            "type": "receipt",
            "data": {"receiving_date": "2024-06-12"}
        }
        dates = get_attachment_dates(attachment)
        self.assertEqual(dates, ["2024-06-12"])

    def test_no_dates(self):
        attachment = {"type": "invoice", "data": {}}
        dates = get_attachment_dates(attachment)
        self.assertEqual(dates, [])


class TestIsDirectionCompatible(unittest.TestCase):
    """Test transaction direction compatibility."""

    def test_outgoing_payment_with_supplier(self):
        # Negative amount (outgoing) should match purchase invoice/receipt
        attachment = {
            "type": "invoice",
            "data": {"supplier": "Vendor", "total_amount": 100.0}
        }
        self.assertTrue(is_direction_compatible(-100.0, attachment))

    def test_outgoing_payment_without_supplier(self):
        # Negative amount with recipient (sales invoice) is incompatible
        attachment = {
            "type": "invoice",
            "data": {"recipient": "Customer", "total_amount": 100.0}
        }
        self.assertFalse(is_direction_compatible(-100.0, attachment))

    def test_incoming_payment_with_recipient(self):
        # Positive amount (incoming) should match sales invoice
        attachment = {
            "type": "invoice",
            "data": {"recipient": "Customer", "total_amount": 100.0}
        }
        self.assertTrue(is_direction_compatible(100.0, attachment))

    def test_incoming_payment_with_supplier(self):
        # Positive amount with supplier is incompatible
        attachment = {
            "type": "invoice",
            "data": {"supplier": "Vendor", "total_amount": 100.0}
        }
        self.assertFalse(is_direction_compatible(100.0, attachment))


if __name__ == "__main__":
    # Run tests with verbose output
    unittest.main(verbosity=2)

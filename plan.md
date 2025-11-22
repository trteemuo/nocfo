# Implementation Plan: Transaction-Attachment Matching

## Overview
This document outlines the strategy for implementing the bidirectional matching logic between bank transactions and attachments (invoices/receipts).

## Critical Data Analysis

### Expected Matches Breakdown:

**Reference-based matches (strongest signal):**
- 2001 → 3001: ref "12345672" matches exactly
- 2002 → 3002: ref "9876 543 2103" vs "98765432103" (whitespace variation)
- 2003 → 3003: ref "0000 0000 5550 0011 14" vs "5550001114" (leading zeros + whitespace)

**Multi-signal matches (no reference):**
- 2004 → 3004: amount 50.00, date exact match (2024-07-15 = due_date), no contact info
- 2005 → 3005: amount 35.00, contact "Matti Meikäläinen" vs supplier "Matti Meikäläinen Tmi"
- 2007 → 3006: amount 1000.00, contact "Best Supplies EMEA" (exact), date exact match with due_date
- 2008 → 3007: amount 324.10 (exact), date exact match with invoicing_date, no contact

**Non-matches (important edge cases):**
- 2006: SAME amount/date as 2005, but "Matti Meittiläinen" ≠ "Matti Meikäläinen" (too different)
- 2009: Has reference "RF661234000001" but NO attachment with matching reference
- 2010: amount 500.50, no matching attachment
- 2011: Positive amount (incoming), reference "RF9027182818284" - no match
- 2012: amount 120.00, contact "Office Mart" - no matching attachment
- 3008: Receipt with reference "RF135550001114" looks close to 2003's "5550001114" but RF13 prefix makes it different
- 3009: Invoice recipient is "ACME Oy" (not Example Company Oy) - wrong company

## Key Challenges

1. **Reference number normalization** - Remove whitespace and leading zeros, but preserve RF prefix
2. **Name similarity threshold** - "Meikäläinen Tmi" should match "Meikäläinen", but "Meittiläinen" should NOT
3. **Date flexibility vs exactness** - Some matches have EXACT date matches, suggesting tight tolerance
4. **Amount + Date combination** - When contact is null, amount + exact date is sufficient (2004, 2008)
5. **Invoice direction detection** - Sales invoices (issuer/recipient) vs purchase invoices (supplier)
6. **Avoid false positives** - 2006 is deliberately a non-match despite being very similar to 2005

## Implementation Strategy

### Phase 1: Helper Functions

#### 1.1 Reference Normalization
```python
def normalize_reference(ref: str | None) -> str | None:
    """Remove whitespace and leading zeros from reference numbers.

    CRITICAL: Preserve RF/FI prefixes! Only strip zeros from the numeric part.
    - "12345672" → "12345672"
    - "9876 543 2103" → "98765432103"
    - "0000 0000 5550 0011 14" → "5550001114"
    - "RF135550001114" → "RF135550001114" (keep RF prefix!)
    """
```
- Return None if input is None or empty
- Strip all whitespace (spaces)
- If starts with letter prefix (RF/FI), preserve it and only strip zeros from numeric part
- Otherwise, remove leading zeros from the entire string
- Handle edge cases: "0000" → "0", empty string after normalization

#### 1.2 Counterparty Extraction
```python
def get_counterparty(attachment: Attachment) -> str | None:
    """Extract the counterparty name from an attachment."""
```
- For sales invoices: return `data.recipient` (customer paying us)
- For purchase invoices: return `data.supplier` (vendor we're paying)
- For receipts: return `data.supplier`
- Handle cases where Example Company Oy appears (skip it, it's us)

#### 1.3 Amount Comparison
```python
def amounts_match(tx_amount: float, att_amount: float) -> bool:
    """Check if transaction and attachment amounts match."""
```
- Consider absolute values (transaction uses negative for outgoing)
- Sales invoices: positive tx amount should match attachment amount
- Purchase invoices/receipts: negative tx amount should match attachment amount
- Allow small floating-point tolerance if needed

#### 1.4 Fuzzy Name Matching
```python
def names_match(name1: str | None, name2: str | None) -> bool:
    """Check if two names are similar enough to be considered a match.

    CRITICAL INSIGHT from data:
    - "Matti Meikäläinen" SHOULD match "Matti Meikäläinen Tmi" (2005 → 3005)
    - "Matti Meittiläinen" should NOT match "Matti Meikäläinen Tmi" (2006 → None)
    - "Best Supplies EMEA" exact match (2007 → 3006)

    Strategy: One name must be a substring of the other (case-insensitive).
    This allows "Meikäläinen" to match "Meikäläinen Tmi" but rejects "Meittiläinen".
    """
```
- Return False if either is None
- Normalize: lowercase, strip leading/trailing whitespace
- Check if one name is contained in the other (bidirectional substring match)
- This naturally handles company suffixes like "Tmi", "Oy", "Ltd", "EMEA"
- Rejects typos/variations that aren't substrings

#### 1.5 Date Proximity
```python
def dates_within_range(date1: str, date2: str, days: int = 0) -> bool:
    """Check if two dates are within N days of each other.

    CRITICAL INSIGHT from data:
    - 2004 → 3004: tx date 2024-07-15 = due_date 2024-07-15 (EXACT)
    - 2007 → 3006: tx date 2024-07-25 = due_date 2024-07-25 (EXACT)
    - 2008 → 3007: tx date 2024-08-02 = invoicing_date 2024-08-02 (EXACT)
    - 2005 → 3005: tx date 2024-07-20, due_date 2024-07-21 (1 day diff)

    All successful matches have 0-1 day difference!
    Start with exact match (0 days), expand to 1-2 days if needed.
    """
```
- Parse ISO date strings (YYYY-MM-DD)
- Calculate absolute difference in days
- Start with days=0 (exact match only)
- May need to expand to 1-2 days based on testing
- For attachments, compare against BOTH invoicing_date AND due_date (whichever is closer)

### Phase 2: Core Matching Logic

#### 2.1 Implement find_attachment()

**Algorithm** (revised based on data analysis):
```
1. FIRST PASS - Reference matching:
   For each attachment in attachments:
     - Normalize both transaction.reference and attachment.data.reference
     - If both exist and normalized values match → return attachment immediately

2. SECOND PASS - Multi-signal matching:
   For each attachment in attachments:
     a. Check amount compatibility:
        - Get absolute values
        - If tx.amount < 0 (outgoing): need purchase invoice or receipt (has "supplier")
        - If tx.amount > 0 (incoming): need sales invoice (has "issuer" and "recipient")
        - Must match attachment.data.total_amount

     b. Check date compatibility:
        - Compare tx.date against attachment dates
        - For invoices: check BOTH invoicing_date AND due_date
        - For receipts: check receiving_date
        - Use dates_within_range() with tight tolerance (0-1 days)

     c. Check counterparty compatibility:
        - Extract counterparty from attachment (use get_counterparty helper)
        - Compare with tx.contact using names_match()
        - Handle None values (tx.contact can be null)

3. Scoring logic:
   - Amount match: REQUIRED (not just +1 point)
   - If amount matches:
     * Both date AND name match: HIGH confidence → return
     * Date matches, name is None: MEDIUM confidence → return if unique
     * Name matches, date close: MEDIUM confidence → return if unique
     * Only amount matches: LOW confidence → return None

4. Handle ambiguity:
   - If multiple attachments have same score, prefer None (avoid false positive)
   - 2005 vs 2006 teaches us: don't guess between similar options

5. Return best match or None
```

**Special considerations**:
- Transaction 2004 and 2008 have null contact but still match (amount + exact date is sufficient)
- Transaction 2006 is deliberately a non-match despite similarity to 2005
- Amount direction MUST be checked (positive vs negative)

#### 2.2 Implement find_transaction()

**Algorithm** (mirror of find_attachment):
```
1. For each transaction in transactions:
   a. Normalize reference numbers for both
   b. If references exist and match → STRONG MATCH, return immediately

2. If no reference match, score each transaction:
   a. Amount match (considering direction): +1 point
   b. Counterparty name match: +1 point
   c. Date within range: +1 point

3. Select transaction with highest score >= threshold
4. Return best match or None
```

### Phase 3: Testing & Refinement

#### 3.1 Initial Testing
```bash
python run.py
```
- Review which matches pass/fail
- Identify patterns in failures

#### 3.2 Iterative Tuning

Based on deep data analysis, expected parameters:
- **Date tolerance**: 0-1 days (all matches are exact or 1 day off)
- **Name matching**: Substring match (handles "Meikäläinen" vs "Meikäläinen Tmi", rejects "Meittiläinen")
- **Reference normalization**: Preserve RF prefix, strip zeros only from numeric portion
- **Scoring**: Amount is REQUIRED, then need date OR name (not simple point system)
- **Floating point tolerance**: Exact match should work, but use abs(diff) < 0.01 for safety

#### 3.3 Edge Cases to Verify

Review the expected matches in run.py:
- **2006 → None**: Similar transaction to 2005 (same amount, date, similar name) - should NOT match due to name variation being too different
- **2009 → None**: Has reference "RF661234000001" but no matching attachment reference
- **2010, 2011, 2012 → None**: No good matches available
- **3008 → None**: Receipt with reference "RF135550001114" - doesn't match transaction 2003's "5550001114" (different format)
- **3009 → None**: Invoice for different company (ACME Oy, not Example Company Oy)

### Phase 4: Code Quality

- Add clear docstrings to all functions
- Add inline comments explaining scoring logic
- Ensure deterministic behavior (no random elements, stable sorting)
- Consider adding type hints for clarity
- Keep functions focused and testable

## Scoring Rubric Alignment

1. **Matching Accuracy**: Multi-signal scoring with clear thresholds prevents false positives
2. **Code Clarity**: Helper functions with clear names and docstrings
3. **Edge Case Handling**: None checks, ambiguous case handling, missing data tolerance
4. **Reusability & Design**: Modular helper functions, configurable thresholds
5. **Documentation**: Docstrings explain reasoning, comments clarify business rules

## Implementation Order

1. Write `normalize_reference()` helper
2. Write `amounts_match()` helper
3. Write `get_counterparty()` helper
4. Write basic `find_attachment()` with reference matching only
5. Test reference matches with `python run.py`
6. Add multi-signal scoring to `find_attachment()`
7. Implement `names_match()` helper
8. Implement `dates_within_range()` helper
9. Test and tune `find_attachment()`
10. Mirror logic to `find_transaction()`
11. Final testing and parameter tuning
12. Add documentation and clean up code

## Critical Insights Summary

After analyzing all test data:

1. **Reference matching is simpler than expected**:
   - Just strip whitespace and leading zeros
   - Preserve letter prefixes (RF, FI)
   - 3 reference matches should work: 2001, 2002, 2003

2. **Date tolerance is TIGHT**:
   - All successful matches have 0-1 day difference
   - Not 14-30 days as initially assumed
   - Check BOTH invoicing_date and due_date for invoices

3. **Name matching uses substring logic**:
   - "Matti Meikäläinen" in "Matti Meikäläinen Tmi" = match
   - "Matti Meittiläinen" not in "Matti Meikäläinen Tmi" = no match
   - Simple contains() check works better than Levenshtein distance

4. **Amount + exact date is sufficient when contact is null**:
   - 2004 and 2008 prove this
   - Don't over-require all three signals

5. **Avoid false positives aggressively**:
   - 2006 is the key test: very similar to 2005 but should return None
   - When in doubt, return None

6. **Direction matters**:
   - Negative tx amount → look for "supplier" field (purchase invoice/receipt)
   - Positive tx amount → look for "recipient" field (sales invoice)
   - Invoice 3009 has recipient="ACME Oy" (not Example Company) so no match

## Success Criteria

The implementation should achieve:
- All expected matches found (2001→3001, 2002→3002, 2003→3003, 2004→3004, 2005→3005, 2007→3006, 2008→3007)
- All expected non-matches return None (2006, 2009, 2010, 2011, 2012, 3008, 3009)
- 100% pass rate on `python run.py` test report (21/21 tests passing)
- Clean, readable, well-documented code
- Deterministic behavior on repeated runs

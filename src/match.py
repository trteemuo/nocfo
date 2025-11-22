from datetime import datetime
from typing import Any

Attachment = dict[str, Any]
Transaction = dict[str, Any]

# Constants for matching logic
AMOUNT_TOLERANCE = 0.01  # Tolerance for floating-point amount comparison (cents)
MIN_SIGNIFICANT_WORD_LENGTH = 2  # Skip short words like "Oy", "AB", "Tmi"
FUZZY_MATCH_WORD_LENGTH_CUTOFF = 6  # Words longer than this allow fuzzy matching
FUZZY_MATCH_THRESHOLD_LONG = 0.85  # Allow 15% character differences for long words
FUZZY_MATCH_THRESHOLD_SHORT = 1.0  # Require exact match for short words
DEFAULT_DATE_TOLERANCE_DAYS = 1  # Banking processing delay tolerance

# Points-based scoring system (normalized to 0.0 - 1.0 scale)
# Award points for each matching criterion, then divide by MAX_POINTS to get confidence percentage
POINTS_DATE_MATCH = 2  # Points awarded when dates match
POINTS_NAME_MATCH = 2  # Points awarded when counterparty names match
POINTS_NULL_CONTACT_BONUS = 1  # Bonus points when contact is null but date matches
MAX_POINTS = POINTS_DATE_MATCH + POINTS_NAME_MATCH + POINTS_NULL_CONTACT_BONUS  # Maximum possible points (sum of all point constants)
MIN_MATCH_CONFIDENCE = 0.4  # Minimum confidence threshold (40%) to accept a match


def normalize_reference(ref: str | None) -> str | None:
    """Remove whitespace and leading zeros from reference numbers.

    Preserves letter prefixes (RF, FI) and only strips zeros from numeric part.

    Examples:
        "12345672" → "12345672"
        "9876 543 2103" → "98765432103"
        "0000 0000 5550 0011 14" → "5550001114"
        "RF135550001114" → "RF135550001114" (keeps RF prefix)
    """
    if ref is None:
        return None

    # Remove all whitespace
    ref = ref.replace(" ", "")

    if not ref:
        return None

    # Check if starts with letter prefix (RF, FI, etc.)
    if ref and ref[0].isalpha():
        # Find where letters end and numbers begin
        i = 0
        while i < len(ref) and ref[i].isalpha():
            i += 1

        if i < len(ref):
            prefix = ref[:i]
            numeric = ref[i:].lstrip("0") or "0"
            return prefix + numeric
        return ref

    # No prefix, just strip leading zeros
    return ref.lstrip("0") or "0"


def get_counterparty(attachment: Attachment) -> str | None:
    """Extract the counterparty name from an attachment.

    For sales invoices: returns recipient (customer)
    For purchase invoices/receipts: returns supplier (vendor)
    """
    data = attachment.get("data", {})

    # Check for supplier field (purchase invoices and receipts)
    if "supplier" in data:
        return data["supplier"]

    # Check for recipient field (sales invoices)
    # The recipient is the customer who paid us
    if "recipient" in data:
        return data["recipient"]

    return None


def amounts_match(amount1: float, amount2: float) -> bool:
    """Check if two amounts match.

    Considers absolute values since transactions use negative for outgoing
    while attachments always use positive values.

    Works regardless of parameter order.
    """
    return abs(abs(amount1) - abs(amount2)) < AMOUNT_TOLERANCE


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate the Levenshtein distance between two strings.

    This measures the minimum number of single-character edits (insertions,
    deletions, or substitutions) needed to change one string into the other.

    Examples:
        levenshtein_distance("kitten", "sitting") → 3
        levenshtein_distance("Meikäläinen", "Meittiläinen") → 2
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    # Create a distance matrix
    previous_row = range(len(s2) + 1)

    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Cost of insertions, deletions, or substitutions
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def names_match(name1: str | None, name2: str | None) -> bool:
    """Check if two names are similar enough to be considered a match.

    Uses a combination of substring matching and fuzzy string matching:
    1. Exact substring match (handles company suffixes)
    2. Fuzzy match using Levenshtein distance (handles spelling variations)

    Examples:
        - "Matti Meikäläinen" matches "Matti Meikäläinen Tmi" ✓ (substring)
        - "Best Supplies" matches "Best Supplies EMEA" ✓ (substring)
        - "Matti Meikäläinen" does NOT match "Matti Meittiläinen" ✗ (different surnames)
    """
    if name1 is None or name2 is None:
        return False

    # Normalize: lowercase and strip whitespace
    n1 = name1.lower().strip()
    n2 = name2.lower().strip()

    # Strategy 1: Exact substring match (handles company suffixes)
    if n1 in n2 or n2 in n1:
        return True

    # Strategy 2: Fuzzy matching for spelling variations
    # Split names into words and compare each word pair
    words1 = n1.split()
    words2 = n2.split()

    # Filter out company suffixes and very short words
    significant_words1 = [w for w in words1 if len(w) > MIN_SIGNIFICANT_WORD_LENGTH]
    significant_words2 = [w for w in words2 if len(w) > MIN_SIGNIFICANT_WORD_LENGTH]

    # If either name has no significant words, can't do fuzzy matching
    if not significant_words1 or not significant_words2:
        return False

    # For each word in the shorter name, try to find a fuzzy match in the other
    shorter_words = significant_words1 if len(significant_words1) <= len(significant_words2) else significant_words2
    longer_words = significant_words2 if len(significant_words1) <= len(significant_words2) else significant_words1

    matched_count = 0
    total_significant_words = len(shorter_words)

    for short_word in shorter_words:
        best_similarity = 0
        for long_word in longer_words:
            # Calculate similarity ratio
            distance = levenshtein_distance(short_word, long_word)
            max_len = max(len(short_word), len(long_word))
            similarity = 1 - (distance / max_len)
            best_similarity = max(best_similarity, similarity)

        # Allow up to 15% character differences for longer words
        # Require exact match for shorter words to avoid false positives
        threshold = FUZZY_MATCH_THRESHOLD_LONG if len(short_word) > FUZZY_MATCH_WORD_LENGTH_CUTOFF else FUZZY_MATCH_THRESHOLD_SHORT

        if best_similarity >= threshold:
            matched_count += 1

    # Require ALL significant words to have fuzzy matches
    # This prevents "Matti Meikäläinen" from matching "Matti Meittiläinen"
    # because while "Matti" matches, "Meikäläinen" won't match "Meittiläinen" (75% similar)
    return matched_count == total_significant_words


def dates_within_range(date1: str, date2: str, days: int = DEFAULT_DATE_TOLERANCE_DAYS) -> bool:
    """Check if two dates are within N days of each other.

    Based on data analysis, successful matches have 0-1 day difference.
    """
    try:
        d1 = datetime.strptime(date1, "%Y-%m-%d")
        d2 = datetime.strptime(date2, "%Y-%m-%d")
        diff = abs((d1 - d2).days)
        return diff <= days
    except (ValueError, TypeError):
        return False


def get_attachment_dates(attachment: Attachment) -> list[str]:
    """Get all relevant dates from an attachment for comparison."""
    data = attachment.get("data", {})
    dates = []

    # For invoices, check both invoicing_date and due_date
    if "invoicing_date" in data:
        dates.append(data["invoicing_date"])
    if "due_date" in data:
        dates.append(data["due_date"])

    # For receipts, check receiving_date
    if "receiving_date" in data:
        dates.append(data["receiving_date"])

    return dates


def is_direction_compatible(tx_amount: float, attachment: Attachment) -> bool:
    """Check if transaction direction matches attachment type.

    Negative tx amount (outgoing) → purchase invoice or receipt (has "supplier")
    Positive tx amount (incoming) → sales invoice (has "recipient")
    """
    data = attachment.get("data", {})

    if tx_amount < 0:
        # Outgoing payment - need supplier field
        return "supplier" in data
    else:
        # Incoming payment - need recipient field (sales invoice)
        return "recipient" in data


def _score_match(
    source_amount: float,
    source_date: str,
    source_contact: str | None,
    source_ref: str | None,
    target_amount: float,
    target_dates: list[str],
    target_counterparty: str | None,
    target_ref: str | None,
    target_item: dict[str, Any],
) -> tuple[float, dict[str, Any]] | None:
    """Score a potential match between a source and target item using normalized confidence (0.0-1.0).

    Args:
        source_amount: Source item's transaction amount
        source_date: Source item's date
        source_contact: Source item's contact name
        source_ref: Source item's reference number
        target_amount: Target item's amount
        target_dates: List of relevant dates from target item
        target_counterparty: Target item's counterparty name
        target_ref: Target item's reference number
        target_item: The actual target dictionary (attachment or transaction)

    Returns:
        Tuple of (confidence_score, target_item) if match is valid, None otherwise.
        Confidence score is normalized between 0.0 and 1.0.
    """
    # Reference matching (strongest signal)
    if source_ref and target_ref and source_ref == target_ref:
        # Reference match is definitive - return perfect confidence
        return (1.0, target_item)

    # Amount must match (REQUIRED for multi-signal matching)
    if not amounts_match(source_amount, target_amount):
        return None

    # Check date compatibility
    date_matches = any(dates_within_range(source_date, target_date) for target_date in target_dates)

    # Check name compatibility
    name_matches = names_match(source_contact, target_counterparty)

    # Points-based scoring (normalized to 0.0-1.0 by dividing by MAX_POINTS):
    # Award points for each matching criterion, then normalize to get confidence percentage
    #
    # Point allocation:
    # - Date match: +2 points
    # - Name match: +2 points
    # - Null contact bonus: +1 point (only when contact missing but date matches)
    # - Maximum: 5 points (auto-calculated: POINTS_DATE_MATCH + POINTS_NAME_MATCH + POINTS_NULL_CONTACT_BONUS)
    #
    # Example scores (points / MAX_POINTS = confidence):
    # - Date + Name: (2+2)/5 = 0.8 confidence (HIGH)
    # - Date + null contact: (2+1)/5 = 0.6 confidence (MEDIUM)
    # - Name only: 2/5 = 0.4 confidence (LOW, at threshold)
    # - Amount only: 0/5 = 0.0 confidence (REJECT)

    points = 0
    if date_matches:
        points += POINTS_DATE_MATCH
    if name_matches:
        points += POINTS_NAME_MATCH
    if (source_contact is None or target_counterparty is None) and date_matches:
        points += POINTS_NULL_CONTACT_BONUS

    # Critical: If BOTH have contacts but they don't match, reject this candidate
    # This prevents false matches like 2006 (Matti Meittiläinen) matching 3005 (Matti Meikäläinen Tmi)
    if source_contact is not None and target_counterparty is not None and not name_matches and date_matches:
        return None  # Name mismatch is disqualifying when both sides have names

    # Normalize points to 0.0-1.0 confidence scale
    confidence = points / MAX_POINTS

    if confidence >= MIN_MATCH_CONFIDENCE:
        return (confidence, target_item)

    return None


def find_attachment(
    transaction: Transaction,
    attachments: list[Attachment],
) -> Attachment | None:
    """Find the best matching attachment for a given transaction.

    Matching strategy:
    1. First try reference number matching (strongest signal) - bypasses all other checks
    2. Then try multi-signal matching (amount + date/name)
    """
    tx_ref = normalize_reference(transaction.get("reference"))
    tx_date = transaction.get("date")
    tx_amount = transaction.get("amount")
    tx_contact = transaction.get("contact")

    # FIRST PASS: Reference matching (bypasses direction and other checks)
    if tx_ref:
        for attachment in attachments:
            att_ref = normalize_reference(attachment.get("data", {}).get("reference"))
            if att_ref and tx_ref == att_ref:
                return attachment

    # SECOND PASS: Multi-signal matching
    candidates = []

    for attachment in attachments:
        data = attachment.get("data", {})

        # Direction must be compatible for multi-signal matching
        if not is_direction_compatible(tx_amount, attachment):
            continue

        # Extract attachment details
        att_ref = normalize_reference(data.get("reference"))
        att_amount = data.get("total_amount", 0)
        att_dates = get_attachment_dates(attachment)
        att_counterparty = get_counterparty(attachment)

        # Score this potential match (skip reference since we're past that phase)
        result = _score_match(
            source_amount=tx_amount,
            source_date=tx_date,
            source_contact=tx_contact,
            source_ref=None,  # Don't use ref in this pass
            target_amount=att_amount,
            target_dates=att_dates,
            target_counterparty=att_counterparty,
            target_ref=None,  # Don't use ref in this pass
            target_item=attachment,
        )

        if result:
            candidates.append(result)

    # If no candidates, return None
    if not candidates:
        return None

    # Sort by score (descending)
    candidates.sort(key=lambda x: x[0], reverse=True)

    # If we have multiple candidates with the same top score, it's ambiguous
    # Return None to avoid false positives
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None

    return candidates[0][1]


def find_transaction(
    attachment: Attachment,
    transactions: list[Transaction],
) -> Transaction | None:
    """Find the best matching transaction for a given attachment.

    Mirrors the logic of find_attachment() in reverse direction.
    """
    data = attachment.get("data", {})
    att_ref = normalize_reference(data.get("reference"))
    att_dates = get_attachment_dates(attachment)
    att_amount = data.get("total_amount", 0)
    att_counterparty = get_counterparty(attachment)

    # FIRST PASS: Reference matching (bypasses direction and other checks)
    if att_ref:
        for transaction in transactions:
            tx_ref = normalize_reference(transaction.get("reference"))
            if tx_ref and att_ref == tx_ref:
                return transaction

    # SECOND PASS: Multi-signal matching
    candidates = []

    for transaction in transactions:
        tx_amount = transaction.get("amount")
        tx_contact = transaction.get("contact")
        tx_date = transaction.get("date")

        # Direction must be compatible for multi-signal matching
        if not is_direction_compatible(tx_amount, attachment):
            continue

        # For find_transaction, we need to check all attachment dates against the single transaction date
        # If attachment has no dates, skip it
        if not att_dates:
            continue

        best_result = None
        for att_date in att_dates:
            result = _score_match(
                source_amount=att_amount,
                source_date=att_date,
                source_contact=att_counterparty,
                source_ref=None,  # Don't use ref in this pass
                target_amount=tx_amount,
                target_dates=[tx_date],
                target_counterparty=tx_contact,
                target_ref=None,  # Don't use ref in this pass
                target_item=transaction,
            )
            if result and (not best_result or result[0] > best_result[0]):
                best_result = result

        if best_result:
            candidates.append(best_result)

    # If no candidates, return None
    if not candidates:
        return None

    # Sort by score (descending)
    candidates.sort(key=lambda x: x[0], reverse=True)

    # If we have multiple candidates with the same top score, it's ambiguous
    # Return None to avoid false positives
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None

    return candidates[0][1]

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext


def calculate_elo_update(ratings, scores):
    """Return four ratings using one six-place, zero-sum team delta."""
    try:
        ratings, scores = tuple(ratings), tuple(scores)
    except TypeError as error:
        raise ValueError("Provide four ratings and two scores.") from error
    if len(ratings) != 4 or len(scores) != 2:
        raise ValueError("Provide four ratings and two scores.")
    if any(isinstance(score, bool) or not isinstance(score, int) or score < 0 for score in scores):
        raise ValueError("Scores must be non-negative integers.")
    if scores[0] == scores[1]:
        raise ValueError("Matches cannot end in a draw.")
    try:
        ratings = tuple(Decimal(str(rating)) for rating in ratings)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Ratings must be finite numbers.") from error
    if any(not rating.is_finite() for rating in ratings):
        raise ValueError("Ratings must be finite numbers.")
    with localcontext() as context:
        context.prec = max(40, max(len(rating.as_tuple().digits) for rating in ratings) + 12)
        difference = ((ratings[2] + ratings[3]) - (ratings[0] + ratings[1])) / Decimal(800)
        # Negative exponents keep large team differences from overflowing.
        odds = context.power(Decimal(10), -abs(difference))
        expected = odds / (1 + odds) if difference >= 0 else 1 / (1 + odds)
        delta = (Decimal(32) * (int(scores[0] > scores[1]) - expected)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_EVEN,
        )
        return (
            ratings[0] + delta, ratings[1] + delta,
            ratings[2] - delta, ratings[3] - delta,
        )

from decimal import Decimal, localcontext
from itertools import permutations

from django.test import SimpleTestCase

from leaderboard.elo import calculate_elo_update


class EloTests(SimpleTestCase):
    def test_equal_teams(self):
        self.assertEqual(
            calculate_elo_update([1000] * 4, [21, 18]),
            (Decimal("1016"), Decimal("1016"), Decimal("984"), Decimal("984")),
        )

    def test_favorites_upsets_and_margin(self):
        ratings = [1400, 1400, 1000, 1000]
        favorite = calculate_elo_update(ratings, [21, 0])
        upset = calculate_elo_update(ratings, [0, 21])
        self.assertEqual(favorite[0], Decimal("1402.909091"))
        self.assertEqual(upset[0], Decimal("1370.909091"))
        self.assertEqual(favorite, calculate_elo_update(ratings, [2, 1]))
        for result in (favorite, upset):
            self.assertEqual(sum(result), sum(ratings))
            self.assertTrue(all(isinstance(value, Decimal) for value in result))

    def test_permutations_and_shared_delta(self):
        for ratings in permutations(map(Decimal, ["912.123456", "1270", "1092.333333", "988"])):
            result = calculate_elo_update(ratings, [15, 12])
            self.assertEqual(sum(result), sum(ratings))
            self.assertEqual(result[0] - ratings[0], result[1] - ratings[1])
            self.assertEqual(result[0] - ratings[0], ratings[2] - result[2])
            swapped = calculate_elo_update(ratings[2:] + ratings[:2], [12, 15])
            self.assertEqual(swapped, result[2:] + result[:2])

    def test_fractional_power_and_decimal_context(self):
        ratings = [Decimal("1000.123456"), Decimal("1101.555555"), Decimal("971.987654"), Decimal("1010")]
        result = calculate_elo_update(ratings, [9, 11])
        with localcontext() as context:
            context.prec = 8
            self.assertEqual(calculate_elo_update(ratings, [9, 11]), result)
        self.assertEqual(sum(result), sum(ratings))
        self.assertEqual(result[0].as_tuple().exponent, -6)

    def test_invalid_inputs(self):
        for ratings in ([], [1000] * 3, [1000] * 5, [1000, 1000, 1000, "nan"],
                        [1000, 1000, 1000, float("inf")], [None] * 4, [True] * 4, None):
            with self.subTest(ratings=ratings), self.assertRaises(ValueError):
                calculate_elo_update(ratings, [21, 15])
        for scores in ([], [21], [21, 15, 0], [1, 1], [-1, 0], [1.5, 0],
                       ["21", 1], [True, 0], [Decimal("21"), 0], None):
            with self.subTest(scores=scores), self.assertRaises(ValueError):
                calculate_elo_update([1000] * 4, scores)

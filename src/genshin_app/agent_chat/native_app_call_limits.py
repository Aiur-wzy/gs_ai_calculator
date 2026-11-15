"""App transport/cache capacity, independent of per-turn consumption budgets."""

# Shared by catalog validation and compact receipt retention. The Node catalog
# mirrors this value and cross-language contract tests guard their equality.
MAX_APP_CALLS = 16_384

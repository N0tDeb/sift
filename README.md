# Sift

A linter for CSV files. Finds the problems that survive a successful load.

    pip install -e .
    sift check orders.csv

## Locale

Half the world writes `1,234.56` and half writes `1.234,56`. Read a German
file with English convention and `12.500,00` becomes `12.5` — a thousandfold
error, no exception raised. Sift reads every number under both conventions
and lets the column decide, the same way it settles date order.
